"""Canonical PIT decision tape builder for Polaris/Orion/Lyra.

Research-only. Uses the canonical replay panel with `ticker == security_id`
projection for existing alpha-lab math, then emits tapes keyed by security_id.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import StrategySpec, run_backtest
from research.canonical_replay_panel import sha256_file, stable_digest
from research.shadow_tracking.strategies import build_strategy_lookup

SCHEMA_VERSION = "caerus_canonical_decision_tape_v1"
PRODUCED_BY = "research.canonical_decision_tape"
DEFAULT_SLEEVES = ("caerus_polaris", "caerus_orion", "caerus_lyra")


@dataclass(frozen=True)
class DecisionTapeBuildResult:
    manifest: dict[str, Any]
    tape_paths: dict[str, str]


def _fallback_spec(sleeve: str) -> StrategySpec:
    if sleeve == "caerus_polaris":
        return StrategySpec(
            name="baseline_top10_daily",
            hypothesis_id="CONTROL",
            description="Caerus Polaris: current paper baseline / operational control.",
            top_n=10,
            rebalance_mode="daily",
        )
    raise ValueError(f"Missing strategy definition for {sleeve}")


def resolve_sleeve_specs(sleeves: tuple[str, ...] = DEFAULT_SLEEVES) -> dict[str, StrategySpec]:
    lookup = build_strategy_lookup()
    specs: dict[str, StrategySpec] = {}
    for sleeve in sleeves:
        specs[sleeve] = lookup[sleeve].spec if sleeve in lookup else _fallback_spec(sleeve)
    return specs


def signal_frame_from_panel(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    required = {"date", "security_id", "display_ticker", "closeadj"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"canonical panel missing columns: {sorted(missing)}")
    display_by_id = (
        panel[["security_id", "display_ticker"]]
        .drop_duplicates()
        .sort_values(["security_id", "display_ticker"])
        .drop_duplicates("security_id", keep="last")
        .set_index("security_id")["display_ticker"]
        .astype(str)
        .to_dict()
    )
    alpha_panel = panel[["date", "security_id", "closeadj"]].rename(
        columns={"security_id": "ticker", "closeadj": "close"}
    )
    return build_alpha_lab_signal_frame(alpha_panel), display_by_id


def _weights_long(weights: pd.DataFrame) -> pd.DataFrame:
    if weights.empty:
        return pd.DataFrame(columns=["trade_date", "security_id", "target_weight"])
    long = weights.stack().rename("target_weight").reset_index()
    long.columns = ["trade_date", "security_id", "target_weight"]
    long["trade_date"] = pd.to_datetime(long["trade_date"]).dt.strftime("%Y-%m-%d")
    long["security_id"] = long["security_id"].astype(str)
    long["target_weight"] = pd.to_numeric(long["target_weight"], errors="coerce").fillna(0.0)
    return long[long["target_weight"] != 0.0]


def build_decision_tape_for_sleeve(
    signals: pd.DataFrame,
    display_by_id: dict[str, str],
    *,
    sleeve: str,
    spec: StrategySpec,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = run_backtest(signals, spec, start_date=start_date, end_date=end_date)
    weights = result.get("weights")
    if not isinstance(weights, pd.DataFrame):
        weights = pd.DataFrame()
    weights_long = _weights_long(weights)

    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[
        (frame["date"] >= pd.Timestamp(start_date))
        & (frame["date"] <= pd.Timestamp(end_date))
        & frame["signal_ready"].astype(bool)
    ].copy()
    frame["trade_date"] = frame["date"].dt.strftime("%Y-%m-%d")
    frame["security_id"] = frame["ticker"].astype(str)
    tape = frame[["trade_date", "security_id", "momentum_rank", "momentum_score"]].rename(
        columns={"momentum_rank": "rank", "momentum_score": "score"}
    )
    tape["ticker"] = tape["security_id"].map(display_by_id).fillna(tape["security_id"])
    tape["sleeve"] = sleeve
    tape["candidate"] = True
    tape = tape.merge(weights_long, on=["trade_date", "security_id"], how="left")
    tape["target_weight"] = tape["target_weight"].fillna(0.0)
    tape["rank"] = pd.to_numeric(tape["rank"], errors="coerce")
    tape["score"] = pd.to_numeric(tape["score"], errors="coerce")
    tape = tape[
        ["trade_date", "security_id", "ticker", "sleeve", "candidate", "rank", "score", "target_weight"]
    ].sort_values(["trade_date", "sleeve", "rank", "security_id"]).reset_index(drop=True)
    summary = {
        "sleeve": sleeve,
        "spec_name": spec.name,
        "row_count": int(len(tape)),
        "trade_date_start": str(tape["trade_date"].min()) if not tape.empty else None,
        "trade_date_end": str(tape["trade_date"].max()) if not tape.empty else None,
        "candidate_security_count": int(tape["security_id"].nunique()) if not tape.empty else 0,
        "selected_row_count": int((tape["target_weight"] > 0).sum()) if not tape.empty else 0,
        "average_selected_count": (
            round(float(tape[tape["target_weight"] > 0].groupby("trade_date")["security_id"].count().mean()), 6)
            if not tape.empty and (tape["target_weight"] > 0).any()
            else 0.0
        ),
    }
    return tape, summary


def build_and_write_decision_tapes(
    *,
    panel_path: Path | str,
    manifest_path: Path | str,
    output_dir: Path | str,
    start_date: str = "2014-01-02",
    end_date: str = "2024-12-31",
    sleeves: tuple[str, ...] = DEFAULT_SLEEVES,
) -> DecisionTapeBuildResult:
    panel_path = Path(panel_path)
    manifest_path = Path(manifest_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(panel_path)
    panel_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    signals, display_by_id = signal_frame_from_panel(panel)
    specs = resolve_sleeve_specs(sleeves)

    tape_paths: dict[str, str] = {}
    tape_hashes: dict[str, str] = {}
    sleeve_summaries: list[dict[str, Any]] = []
    for sleeve, spec in specs.items():
        tape, summary = build_decision_tape_for_sleeve(
            signals,
            display_by_id,
            sleeve=sleeve,
            spec=spec,
            start_date=start_date,
            end_date=end_date,
        )
        path = output_dir / f"decision_tape_{sleeve}.parquet"
        tape.to_parquet(path, index=False)
        tape_paths[sleeve] = str(path)
        tape_hashes[sleeve] = sha256_file(path)
        sleeve_summaries.append(summary)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "produced_by": PRODUCED_BY,
        "universe_method": panel_manifest.get("universe_method"),
        "membership_family": panel_manifest.get("membership_family"),
        "membership_scale_precision": panel_manifest.get("membership_scale_precision"),
        "decision_grade_blockers": panel_manifest.get("decision_grade_blockers", []),
        "price_panel_path": str(panel_path),
        "price_panel_sha256": sha256_file(panel_path),
        "price_manifest_path": str(manifest_path),
        "price_manifest_sha256": sha256_file(manifest_path),
        "identity_key": "security_id",
        "ticker_role": "display_only",
        "start_date": start_date,
        "end_date": end_date,
        "sleeves": sleeve_summaries,
        "tape_paths": tape_paths,
        "tape_hashes": tape_hashes,
    }
    manifest["lineage_digest"] = stable_digest(manifest)
    manifest_path_out = output_dir / "decision_tape_manifest.json"
    manifest_path_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tape_paths["manifest"] = str(manifest_path_out)
    return DecisionTapeBuildResult(manifest=manifest, tape_paths=tape_paths)
