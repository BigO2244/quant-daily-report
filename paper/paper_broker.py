# paper/paper_broker.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class PaperConfig:
    initial_equity: float
    benchmark_ticker: str
    slippage_bps: float
    allow_fractional_shares: bool
    min_trade_dollars: float


def load_config(config_path: str) -> PaperConfig:
    """Load paper trading config from JSON."""
    with open(config_path, "r") as f:
        cfg = json.load(f)

    return PaperConfig(
        initial_equity=float(cfg["initial_equity"]),
        benchmark_ticker=str(cfg["benchmark_ticker"]),
        slippage_bps=float(cfg["execution"]["slippage_bps"]),
        allow_fractional_shares=bool(cfg["constraints"]["allow_fractional_shares"]),
        min_trade_dollars=float(cfg["constraints"]["min_trade_dollars"]),
    )


def ensure_scaffold_files(repo_root: str) -> Dict[str, str]:
    """
    Ensures paper trading scaffold files exist.
    Does NOT write to ledger/trades yet—just checks presence.
    """
    paths = {
        "config": os.path.join(repo_root, "paper", "config_paper.json"),
        "ledger": os.path.join(repo_root, "paper", "ledger.csv"),
        "trades": os.path.join(repo_root, "paper", "trades.csv"),
        "holdings_latest": os.path.join(repo_root, "paper", "holdings_latest.csv"),
    }

    missing = [k for k, p in paths.items() if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            f"Missing expected paper scaffold files: {missing}. "
            f"Expected at: {', '.join([paths[m] for m in missing])}"
        )
    return paths


def smoke_test(repo_root: str = ".") -> Dict[str, Any]:
    """
    Smoke test: validates config loads and scaffold files exist.
    Returns a dict summary for logging.
    """
    paths = ensure_scaffold_files(repo_root)
    cfg = load_config(paths["config"])

    return {
        "ok": True,
        "initial_equity": cfg.initial_equity,
        "benchmark_ticker": cfg.benchmark_ticker,
        "slippage_bps": cfg.slippage_bps,
        "allow_fractional_shares": cfg.allow_fractional_shares,
        "min_trade_dollars": cfg.min_trade_dollars,
        "paths": paths,
    }


if __name__ == "__main__":
    result = smoke_test(".")
    print("✅ Paper broker smoke test passed")
    print(json.dumps(result, indent=2))
