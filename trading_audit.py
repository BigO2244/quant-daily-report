"""Deterministic trading turnover and cost audit."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ALLOWLIST_DIRS = ["outputs", "reports", "backtests"]


@dataclass
class ArtifactInfo:
    """Discovered artifact with deterministic sort key."""

    path: Path
    date: str | None  # YYYY-MM-DD if parsable, else None
    lexical_path: str

    @classmethod
    def from_path(cls, path: Path) -> ArtifactInfo:
        """Extract date and lexical sort key from path."""
        filename = path.name
        date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
        date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}" if date_match else None
        return cls(
            path=path,
            date=date,
            lexical_path=str(path).replace(str(path.parent.parent), "").lstrip("/"),
        )

    def sort_key(self) -> tuple[str, str]:
        """Return (reversed_date_or_empty, lexical_path) for deterministic sorting."""
        # Prefer newest date (reverse sort), then lexicographic
        if self.date:
            return (self.date, self.lexical_path)
        return ("", self.lexical_path)


def discover_artifacts(repo_root: Path, until_date: str | None = None) -> dict[str, ArtifactInfo]:
    """Discover trading artifacts deterministically.
    
    Args:
        repo_root: Repository root path
        until_date: Optional YYYY-MM-DD cutoff; only include artifacts on or before this date
    
    Returns:
        Dict mapping artifact type to ArtifactInfo
    """
    artifacts: dict[str, list[ArtifactInfo]] = defaultdict(list)

    for allow_dir in ALLOWLIST_DIRS:
        dir_path = repo_root / allow_dir
        if not dir_path.exists():
            continue

        for item in sorted(dir_path.rglob("*")):
            if not item.is_file():
                continue

            name = item.name
            info = ArtifactInfo.from_path(item)

            # Filter by date if specified
            if until_date and info.date and info.date > until_date:
                continue

            if name == "trades.csv":
                artifacts["trades"].append(info)
            elif name.startswith("holdings_") and name.endswith(".csv"):
                artifacts["holdings"].append(info)
            elif name.startswith("positions_") and name.endswith(".csv"):
                artifacts["positions"].append(info)
            elif name.startswith("ledger_write_") and name.endswith(".json"):
                artifacts["ledger"].append(info)

    # Select best artifact for each type: prefer newest date, break ties lexicographically
    result = {}
    for key, items in artifacts.items():
        if items:
            # Sort by (date DESC, lexical_path ASC) - newer dates first, then lexicographic
            best = sorted(items, key=lambda x: (x.date or "", x.lexical_path), reverse=True)[0]
            result[key] = best

    return result


def read_trades(path: Path) -> list[dict[str, Any]]:
    """Read trades CSV and return list of trade dicts."""
    if not path.exists():
        return []

    trades = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["quantity"] = float(row.get("quantity", 0))
            row["fill_price"] = float(row.get("fill_price", 0))
            row["notional"] = float(row.get("notional", 0))
            row["fees"] = float(row.get("fees", 0))
            trades.append(row)
    return trades


def read_holdings(path: Path) -> dict[str, float]:
    """Read holdings CSV; return dict of ticker -> shares."""
    if not path.exists():
        return {}

    holdings = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip()
            shares = float(row.get("shares", 0))
            if ticker:
                holdings[ticker] = shares
    return holdings


def read_positions(path: Path) -> dict[str, float]:
    """Read positions CSV; return dict of ticker -> shares."""
    return read_holdings(path)  # Same format


def group_trades_by_date(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group trades by trade_date."""
    grouped = defaultdict(list)
    for trade in trades:
        date = trade.get("trade_date", "").strip()
        if date:
            grouped[date].append(trade)
    return dict(sorted(grouped.items()))


def calculate_turnover(trades_by_date: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Calculate annualized and weekly turnover metrics."""
    if not trades_by_date:
        return {
            "status": "N/A",
            "reason": "No trades data available",
            "annualized_turnover_pct": None,
            "weekly_stats": None,
        }

    # Group by week
    weekly_turnover = defaultdict(float)
    for date_str, date_trades in trades_by_date.items():
        total_notional = sum(abs(t["notional"]) for t in date_trades)
        weekly_turnover[date_str[:7]] += total_notional  # Group by YYYY-MM (rough week proxy)

    if not weekly_turnover:
        return {
            "status": "N/A",
            "reason": "No trade notionals",
            "annualized_turnover_pct": None,
            "weekly_stats": None,
        }

    weekly_values = sorted(weekly_turnover.values())
    mean_weekly = sum(weekly_values) / len(weekly_values) if weekly_values else 0
    median_weekly = weekly_values[len(weekly_values) // 2] if weekly_values else 0
    p90_weekly = weekly_values[int(len(weekly_values) * 0.9)] if weekly_values else 0

    annualized = mean_weekly * 52  # Rough annualization

    return {
        "status": "OK",
        "annualized_turnover_pct": round(annualized, 2),
        "weekly_stats": {
            "mean": round(mean_weekly, 2),
            "median": round(median_weekly, 2),
            "p90": round(p90_weekly, 2),
        },
    }


def calculate_holding_period(trades_by_date: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Calculate average holding period."""
    if not trades_by_date:
        return {"status": "N/A", "reason": "No trades data", "avg_holding_days": None}

    unique_dates = sorted(trades_by_date.keys())
    if len(unique_dates) < 2:
        return {"status": "N/A", "reason": "Insufficient trade dates", "avg_holding_days": None}

    first_date = datetime.strptime(unique_dates[0], "%Y-%m-%d")
    last_date = datetime.strptime(unique_dates[-1], "%Y-%m-%d")
    total_days = (last_date - first_date).days
    num_rebalances = len(unique_dates)

    avg_holding = total_days / num_rebalances if num_rebalances > 0 else 0

    return {
        "status": "OK",
        "avg_holding_days": round(avg_holding, 1),
        "period_start": unique_dates[0],
        "period_end": unique_dates[-1],
        "num_rebalances": num_rebalances,
    }


def calculate_slippage_table(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate slippage sensitivity table for [0, 5, 10, 20] bps."""
    if not trades:
        return {"status": "N/A", "reason": "No trades", "table": None}

    total_notional = sum(abs(t["notional"]) for t in trades)
    actual_fees = sum(t.get("fees", 0) for t in trades)

    bps_levels = [0, 5, 10, 20]
    table = {}
    for bps in bps_levels:
        cost = (total_notional * bps / 10000) if bps > 0 else 0
        total_cost = actual_fees + cost
        cost_pct = (total_cost / total_notional * 100) if total_notional > 0 else 0
        table[bps] = round(cost_pct, 3)

    return {
        "status": "OK",
        "total_notional": round(total_notional, 2),
        "actual_fees": round(actual_fees, 4),
        "sensitivity_table": table,
    }


def calculate_concentration(holdings: dict[str, float]) -> dict[str, Any]:
    """Calculate concentration metrics: max position %, HHI."""
    if not holdings:
        return {"status": "N/A", "reason": "No holdings", "max_position_pct": None, "hhi": None}

    total = sum(holdings.values())
    if total == 0:
        return {"status": "N/A", "reason": "Zero total holdings", "max_position_pct": None, "hhi": None}

    weights = [(v / total) for v in holdings.values()]
    max_weight = max(weights) * 100 if weights else 0

    hhi = sum(w**2 for w in weights) * 10000

    return {
        "status": "OK",
        "max_position_pct": round(max_weight, 2),
        "hhi": round(hhi, 0),
        "num_positions": len(holdings),
    }


def generate_report(
    artifacts: dict[str, ArtifactInfo],
    asof_date: str | None = None,
) -> str:
    """Generate markdown report with all audit sections."""
    lines = [
        "# Trading Turnover and Cost Audit Report",
        "",
        f"**Generated:** {datetime.now().isoformat()}",
        f"**Analysis Date:** {asof_date or 'Latest'}",
        "",
        "## Executive Summary",
        "",
        "| Metric | Value | Status |",
        "|--------|-------|--------|",
    ]

    # Read data
    trades = read_trades(artifacts.get("trades").path) if artifacts.get("trades") else []
    holdings_path = artifacts.get("holdings").path if artifacts.get("holdings") else None
    holdings = read_holdings(holdings_path) if holdings_path else {}

    # Calculate metrics
    trades_by_date = group_trades_by_date(trades)
    turnover = calculate_turnover(trades_by_date)
    holding_period = calculate_holding_period(trades_by_date)
    slippage = calculate_slippage_table(trades)
    concentration = calculate_concentration(holdings)

    # Summary table
    lines.append(
        f"| Annualized Turnover | {turnover.get('annualized_turnover_pct', 'N/A')}% | {turnover['status']} |"
    )
    lines.append(
        f"| Avg Holding Period | {holding_period.get('avg_holding_days', 'N/A')} days | {holding_period['status']} |"
    )
    lines.append(f"| Max Position | {concentration.get('max_position_pct', 'N/A')}% | {concentration['status']} |")
    lines.append(f"| HHI Index | {concentration.get('hhi', 'N/A')} | {concentration['status']} |")
    lines.append("")

    # Turnover section
    lines.extend(
        [
            "## Turnover Analysis",
            "",
        ]
    )
    if turnover["status"] == "OK":
        lines.append(f"**Annualized Turnover:** {turnover['annualized_turnover_pct']}%")
        lines.append("")
        lines.append("**Weekly Distribution:**")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k, v in turnover["weekly_stats"].items():
            lines.append(f"| {k.capitalize()} | {v} |")
    else:
        lines.append(f"**Status:** {turnover['status']}")
        lines.append(f"**Reason:** {turnover.get('reason', 'Unknown')}")
    lines.append("")

    # Holding period section
    lines.extend(
        [
            "## Holding Period Analysis",
            "",
        ]
    )
    if holding_period["status"] == "OK":
        lines.append(f"**Average Holding Period:** {holding_period['avg_holding_days']} days")
        lines.append(f"**Period:** {holding_period['period_start']} to {holding_period['period_end']}")
        lines.append(f"**Number of Rebalances:** {holding_period['num_rebalances']}")
    else:
        lines.append(f"**Status:** {holding_period['status']}")
        lines.append(f"**Reason:** {holding_period.get('reason', 'Unknown')}")
    lines.append("")

    # Slippage section
    lines.extend(
        [
            "## Slippage Sensitivity Analysis",
            "",
        ]
    )
    if slippage["status"] == "OK":
        lines.append(f"**Total Notional Traded:** ${slippage['total_notional']:,.2f}")
        lines.append(f"**Actual Fees:** ${slippage['actual_fees']:,.4f}")
        lines.append("")
        lines.append("**Cost Sensitivity (% of notional):**")
        lines.append("| Slippage (bps) | Total Cost % |")
        lines.append("|---|---|")
        for bps, cost_pct in slippage["sensitivity_table"].items():
            lines.append(f"| {bps} | {cost_pct}% |")
    else:
        lines.append(f"**Status:** {slippage['status']}")
        lines.append(f"**Reason:** {slippage.get('reason', 'Unknown')}")
    lines.append("")

    # Concentration section
    lines.extend(
        [
            "## Concentration Analysis",
            "",
        ]
    )
    if concentration["status"] == "OK":
        lines.append(f"**Maximum Position:** {concentration['max_position_pct']}%")
        lines.append(f"**HHI Index:** {concentration['hhi']}")
        lines.append(f"**Number of Positions:** {concentration['num_positions']}")
    else:
        lines.append(f"**Status:** {concentration['status']}")
        lines.append(f"**Reason:** {concentration.get('reason', 'Unknown')}")
    lines.append("")

    # Deployment gates
    lines.extend(
        [
            "## Deployment Gates",
            "",
            "| Gate | Status | Notes |",
            "|------|--------|-------|",
        ]
    )
    turnover_ok = turnover.get("annualized_turnover_pct", 0) and turnover["annualized_turnover_pct"] < 500
    holding_ok = holding_period.get("avg_holding_days", 0) and holding_period["avg_holding_days"] > 3
    concentration_ok = concentration.get("max_position_pct", 0) and concentration["max_position_pct"] < 50
    hhi_ok = concentration.get("hhi", 0) and concentration["hhi"] < 3000

    lines.append(f"| Turnover < 500% | {'PASS' if turnover_ok else 'FAIL'} | Annualized: {turnover.get('annualized_turnover_pct', 'N/A')}% |")
    lines.append(f"| Avg Holding > 3 days | {'PASS' if holding_ok else 'FAIL'} | {holding_period.get('avg_holding_days', 'N/A')} days |")
    lines.append(f"| Max Position < 50% | {'PASS' if concentration_ok else 'FAIL'} | {concentration.get('max_position_pct', 'N/A')}% |")
    lines.append(f"| HHI < 3000 | {'PASS' if hhi_ok else 'FAIL'} | {concentration.get('hhi', 'N/A')} |")
    lines.append("")

    all_gates_pass = turnover_ok and holding_ok and concentration_ok and hhi_ok
    lines.append(f"**Overall Status:** {'✓ PASS' if all_gates_pass else '✗ FAIL'}")
    lines.append("")

    # Artifact info
    lines.extend(
        [
            "## Artifact Sources",
            "",
        ]
    )
    for artifact_type, info in sorted(artifacts.items()):
        lines.append(f"- **{artifact_type.title()}:** {info.path}")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="trading_audit",
        description="Deterministic trading turnover and cost audit",
    )
    parser.add_argument("--asof", help="Analysis date (YYYY-MM-DD)")
    parser.add_argument("--out", help="Output report path (default: stdout)")
    parser.add_argument("--discover", action="store_true", help="Discover artifacts and exit")
    parser.add_argument("--print-json", action="store_true", help="Print discovery results as JSON")

    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    artifacts = discover_artifacts(repo_root, until_date=args.asof)

    if args.discover:
        if args.print_json:
            result = {
                artifact_type: {
                    "path": str(info.path),
                    "date": info.date,
                    "lexical_path": info.lexical_path,
                }
                for artifact_type, info in artifacts.items()
            }
            print(json.dumps(result, indent=2))
        else:
            for artifact_type, info in sorted(artifacts.items()):
                print(f"{artifact_type}: {info.path} (date: {info.date})")
        return 0

    # Generate and write report
    report = generate_report(artifacts, asof_date=args.asof)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"Report written to {out_path}", file=sys.stderr)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
