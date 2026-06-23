#!/usr/bin/env python3
"""Build `caerus_large_cap` from a SHARADAR/DAILY market-cap cache.

Research-only.  The output rows use `scale_source=marketcap`, which is the only
large-cap scale source accepted by the canonical replay certifier as PIT-exact.
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
    build_daily_marketcap_membership,
    load_daily_marketcap_cache,
    sha256_file,
    stable_digest,
)
from research.pit_large_cap_family import DEFAULT_MIN_MARKETCAP  # noqa: E402

FIELDS = [
    "security_id",
    "ticker",
    "membership_family",
    "membership_start_date",
    "membership_end_date",
    "scale_source",
    "source",
    "confidence",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build date-effective large-cap family from DAILY marketcap.")
    parser.add_argument("--security-master", default="data/pit_universe/security_master.csv")
    parser.add_argument("--daily-cache-dir", default="data/research_cache/sharadar_daily_marketcap")
    parser.add_argument("--output", default="data/pit_universe/membership_universe_large_cap.csv")
    parser.add_argument("--summary-out", default="outputs/research/fr068_marketcap_reconstruction/daily_marketcap_family_summary.json")
    parser.add_argument("--min-marketcap", type=float, default=DEFAULT_MIN_MARKETCAP)
    parser.add_argument("--min-security-count", type=int, default=1000)
    args = parser.parse_args(argv)

    master_path = Path(args.security_master)
    cache_dir = Path(args.daily_cache_dir)
    master = pd.read_csv(master_path, dtype=str)
    daily = load_daily_marketcap_cache(cache_dir, master)
    membership = build_daily_marketcap_membership(
        security_master=master,
        daily_marketcap=daily,
        min_marketcap=args.min_marketcap,
    )
    security_count = int(membership["security_id"].nunique()) if not membership.empty else 0
    status = "PASS" if security_count >= args.min_security_count else "FAIL"
    blockers = [] if status == "PASS" else ["DAILY_MARKETCAP_MEMBERSHIP_SECURITY_COUNT_BELOW_THRESHOLD"]

    out_path = Path(args.output)
    if status == "PASS":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        membership[FIELDS].to_csv(out_path, index=False)

    summary = {
        "schema_version": "caerus_daily_marketcap_large_cap_family_v1",
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": status,
        "blockers": blockers,
        "security_master": str(master_path),
        "daily_cache_dir": str(cache_dir),
        "daily_marketcap_rows": int(len(daily)),
        "membership_rows": int(len(membership)),
        "membership_security_count": security_count,
        "min_marketcap": args.min_marketcap,
        "min_security_count": args.min_security_count,
        "output": str(out_path) if status == "PASS" else None,
        "scale_source": "marketcap",
        "security_master_sha256": sha256_file(master_path),
    }
    if status == "PASS":
        summary["output_sha256"] = sha256_file(out_path)
    summary["digest"] = stable_digest(summary)
    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "blockers": blockers,
        "daily_marketcap_rows": summary["daily_marketcap_rows"],
        "membership_rows": summary["membership_rows"],
        "membership_security_count": security_count,
        "output": summary["output"],
        "summary": str(summary_path),
    }, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
