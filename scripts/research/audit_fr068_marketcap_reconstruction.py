#!/usr/bin/env python3
"""Audit FR-068 PIT market-cap reconstruction feasibility (RESEARCH_ONLY).

This script does not modify canonical universe artifacts.  It writes a data
source inventory and a coverage diagnostic showing whether local filings plus
SEP close can reconstruct PIT market cap.  If a daily market-cap panel is
supplied, it also builds a candidate date-effective membership file under the
requested output directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.fr068_marketcap_reconstruction import (  # noqa: E402
    DATE_CHECKPOINTS,
    SCHEMA_VERSION,
    build_daily_marketcap_membership,
    discover_source_inventory,
    evaluate_filing_based_marketcap_coverage,
    load_daily_marketcap_panel,
    sha256_file,
    stable_digest,
)
from research.pit_large_cap_family import DEFAULT_MIN_MARKETCAP  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-068 market-cap reconstruction audit.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-dir", default="outputs/research/fr068_marketcap_reconstruction/2026-06-22")
    parser.add_argument("--dates", default=",".join(DATE_CHECKPOINTS))
    parser.add_argument("--min-marketcap", type=float, default=DEFAULT_MIN_MARKETCAP)
    parser.add_argument("--daily-marketcap-file", default=None)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dates = tuple(d.strip() for d in args.dates.split(",") if d.strip())

    inventory = discover_source_inventory(repo_root).to_dict()
    coverage = evaluate_filing_based_marketcap_coverage(
        repo_root=repo_root,
        as_of_dates=dates,
        min_marketcap=args.min_marketcap,
    )
    coverage_path = output_dir / "filing_marketcap_coverage.csv"
    coverage.to_csv(coverage_path, index=False)

    candidate_membership_path = None
    candidate_membership_rows = 0
    candidate_status = "NOT_ATTEMPTED_NO_DAILY_MARKETCAP_FILE"
    candidate_digest = None
    if args.daily_marketcap_file:
        master = pd.read_csv(repo_root / "data" / "pit_universe" / "security_master.csv", dtype=str)
        daily = load_daily_marketcap_panel(args.daily_marketcap_file)
        candidate = build_daily_marketcap_membership(
            security_master=master,
            daily_marketcap=daily,
            min_marketcap=args.min_marketcap,
        )
        candidate_membership_path = output_dir / "membership_universe_large_cap_daily_marketcap_candidate.csv"
        candidate.to_csv(candidate_membership_path, index=False)
        candidate_membership_rows = int(len(candidate))
        candidate_status = "BUILT_FROM_DAILY_MARKETCAP"
        candidate_digest = sha256_file(candidate_membership_path)

    can_replace = (
        inventory["daily_marketcap_candidate_file_count"] > 0
        or args.daily_marketcap_file is not None
    ) and candidate_status == "BUILT_FROM_DAILY_MARKETCAP"
    filing_min_coverage = (
        float(coverage["reconstructable_marketcap_coverage_pct"].min())
        if not coverage.empty else 0.0
    )
    status = "PASS" if can_replace else "FAIL"
    blockers: list[str] = []
    if not can_replace:
        blockers.append("SHARADAR_DAILY_MARKETCAP_PANEL_MISSING")
    if filing_min_coverage < 0.999:
        blockers.append("FILING_BASED_SHARE_COUNT_COVERAGE_INSUFFICIENT")

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": status,
        "blockers": sorted(set(blockers)),
        "inventory": inventory,
        "filing_based_marketcap_coverage_path": str(coverage_path),
        "filing_based_marketcap_min_coverage_pct": round(filing_min_coverage, 6),
        "daily_marketcap_candidate_status": candidate_status,
        "daily_marketcap_candidate_path": str(candidate_membership_path) if candidate_membership_path else None,
        "daily_marketcap_candidate_rows": candidate_membership_rows,
        "daily_marketcap_candidate_sha256": candidate_digest,
    }
    report["digest"] = stable_digest(report)
    report_path = output_dir / "fr068_marketcap_reconstruction_audit.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": status,
        "blockers": report["blockers"],
        "filing_based_marketcap_min_coverage_pct": report["filing_based_marketcap_min_coverage_pct"],
        "daily_marketcap_candidate_status": candidate_status,
        "report": str(report_path),
    }, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
