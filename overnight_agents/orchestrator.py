"""
Overnight Agent Orchestrator
===============================
Runs all research agents after market close and writes a single
daily signals JSON to outputs/overnight_signals/YYYY-MM-DD.json.

This file is consumed at 7 AM ET by the alpha stack precompute pipeline
via the thematic overlay and (future) regime engine extensions.

Usage:
    python overnight_agents/orchestrator.py
    python overnight_agents/orchestrator.py --date 2026-04-17
    python overnight_agents/orchestrator.py --agents liquidity gamma etf_flows
    python overnight_agents/orchestrator.py --dry-run

Schedule: Run nightly at 8:00 PM ET via cron or GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("orchestrator")

_OUTPUT_DIR = Path(__file__).parent.parent / "outputs" / "overnight_signals"


def _build_agents():
    """Instantiate all agents. Import here to keep startup fast."""
    from overnight_agents.liquidity import LiquidityAgent
    from overnight_agents.gamma_regime import GammaRegimeAgent
    from overnight_agents.etf_flows import ETFFlowsAgent
    from overnight_agents.earnings_revision import EarningsRevisionAgent
    from overnight_agents.commodity import CommodityAgent
    from overnight_agents.insider_activity import InsiderActivityAgent
    from overnight_agents.sentiment import SentimentAgent

    return [
        LiquidityAgent(),
        GammaRegimeAgent(),
        ETFFlowsAgent(),
        EarningsRevisionAgent(),
        CommodityAgent(),
        InsiderActivityAgent(),
        SentimentAgent(),
    ]


def run_all(
    as_of_date: Optional[date] = None,
    agent_filter: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict:
    """
    Run all (or a subset of) overnight agents and return the signals dict.

    Parameters
    ----------
    as_of_date : date, optional
        Target date. Defaults to today.
    agent_filter : list of str, optional
        If set, only run agents whose name is in this list.
    dry_run : bool
        If True, skip writing output file.

    Returns
    -------
    dict — full signals payload (also written to disk unless dry_run)
    """
    target = as_of_date or date.today()
    agents = _build_agents()

    if agent_filter:
        agents = [a for a in agents if a.name in agent_filter]
        logger.info("Running subset: %s", [a.name for a in agents])

    signals: List[dict] = []
    for agent in agents:
        logger.info("→ Running %s agent...", agent.name)
        try:
            result = agent.run(target)
        except Exception:
            logger.exception("[ORCHESTRATOR] agent %s raised unexpectedly — using neutral stub", agent.name)
            result = {
                "agent": agent.name,
                "signal": None,
                "confidence": None,
                "as_of": None,
                "status": "error",
                "regime": "unknown",
                "score": 0.0,
                "error": "unhandled exception in orchestrator",
            }
        result.setdefault("agent", agent.name)
        result.setdefault("signal", None)
        result.setdefault("confidence", None)
        result.setdefault("as_of", target.isoformat())
        signals.append(result)
        status = result.get("status", "?")
        regime = result.get("regime", "?")
        score = result.get("score", 0.0)
        logger.info("  %s: status=%s regime=%s score=%.3f", agent.name, status, regime, score)

    signals_by_name: Dict[str, dict] = {r.get("agent", ""): r for r in signals}
    payload = {
        "as_of_date": target.isoformat(),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "agent_count": len(signals),
        "signals": signals,
        "summary": _build_summary(signals_by_name),
    }

    if not dry_run:
        _persist(payload, target)
    else:
        logger.info("[DRY RUN] Output not written.")
        print(json.dumps(payload, indent=2))

    return payload


def _build_summary(signals: Dict[str, dict]) -> dict:
    """Build a human-readable summary across all agent outputs."""
    ok = [k for k, v in signals.items() if v.get("status") == "ok"]
    errors = [k for k, v in signals.items() if v.get("status") == "error"]

    # Aggregate regime calls
    regimes = {k: v.get("regime", "unknown") for k, v in signals.items()}

    # Risk-on vs risk-off vote count
    risk_on_signals = [
        k for k, v in signals.items()
        if any(x in v.get("regime", "") for x in ["expanding", "positive_gamma", "risk_on"])
    ]
    risk_off_signals = [
        k for k, v in signals.items()
        if any(x in v.get("regime", "") for x in ["contracting", "negative_gamma", "risk_off",
                                                    "inflationary_shock", "extreme_bullish"])
    ]

    net_signal = len(risk_on_signals) - len(risk_off_signals)
    if net_signal >= 2:
        overall = "risk_on"
    elif net_signal <= -2:
        overall = "risk_off"
    else:
        overall = "neutral"

    return {
        "overall_regime": overall,
        "agents_ok": ok,
        "agents_error": errors,
        "risk_on_signals": risk_on_signals,
        "risk_off_signals": risk_off_signals,
        "regimes": regimes,
    }


def _persist(payload: dict, as_of: date) -> Path:
    """Write payload to outputs/overnight_signals/YYYY-MM-DD.json."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUT_DIR / f"{as_of.isoformat()}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Signals written: %s", out_path)
    return out_path


def load_latest(as_of: Optional[date] = None, max_age_days: int = 3) -> Optional[dict]:
    """
    Load the most recent overnight signals file.
    Used by the alpha stack thematic overlay and regime engine.
    """
    from datetime import timedelta
    target = as_of or date.today()
    cutoff = target - timedelta(days=max_age_days)

    if not _OUTPUT_DIR.exists():
        return None

    candidates = sorted(
        [(date.fromisoformat(f.stem), f) for f in _OUTPUT_DIR.glob("*.json")
         if f.stem.count("-") == 2],
        reverse=True,
    )
    for file_date, path in candidates:
        if file_date >= cutoff:
            try:
                return json.loads(path.read_text())
            except Exception:
                continue
    return None


def parse_args():
    p = argparse.ArgumentParser(description="Overnight Research Agent Orchestrator")
    p.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    p.add_argument("--agents", nargs="+", default=None,
                   help="Run only these agents (default: all)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print output; do not write to disk")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    as_of = date.fromisoformat(args.date) if args.date else None
    run_all(
        as_of_date=as_of,
        agent_filter=args.agents,
        dry_run=args.dry_run,
    )
