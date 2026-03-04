# research/vix_regime.py
"""
VIX Regime Detection
====================
Classifies current market volatility into one of four regimes and
returns a position-scale factor used by Sleeve Trend to reduce exposure
during high-volatility periods — without requiring options or new instruments.

Regime table (thresholds sourced from sleeves/sleeve_trend/config.py):

    LOW       VIX < 20   scale=1.00  max_positions=10   Full deployment
    ELEVATED  VIX < 30   scale=0.75  max_positions=7    Cautious
    HIGH      VIX < 40   scale=0.50  max_positions=4    Defensive
    CRISIS    VIX >= 40  scale=0.25  max_positions=2    Near-cash

Usage (standalone):
    from research.vix_regime import get_current_regime
    regime = get_current_regime()
    # {'regime': 'LOW', 'vix': 14.3, 'position_scale': 1.0, 'max_positions': 10}

Usage (with override for backtesting):
    regime = get_current_regime(vix_override=32.5)
    # {'regime': 'HIGH', 'vix': 32.5, 'position_scale': 0.5, 'max_positions': 4}

Outputs written to:
    outputs/vix_regime/regime_current.json   — latest regime snapshot
    outputs/vix_regime/regime_history.csv    — daily log for trend analysis
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
OUTPUT_DIR   = Path("outputs/vix_regime")
REGIME_JSON  = OUTPUT_DIR / "regime_current.json"
REGIME_CSV   = OUTPUT_DIR / "regime_history.csv"

# ---------------------------------------------------------------------------
# Regime table — built from sleeve_trend config so thresholds stay in sync.
# Each entry: (label, vix_lo_inclusive, vix_hi_exclusive, scale, max_positions)
# ---------------------------------------------------------------------------
def _build_regime_table() -> list[tuple[str, float, float, float, int]]:
    try:
        from sleeves.sleeve_trend import config as cfg
        return [
            ("LOW",      0.0,                        cfg.VIX_LOW_THRESHOLD,       cfg.VIX_SCALE_LOW,      cfg.VIX_MAX_POSITIONS_LOW),
            ("ELEVATED", cfg.VIX_LOW_THRESHOLD,      cfg.VIX_ELEVATED_THRESHOLD,  cfg.VIX_SCALE_ELEVATED, cfg.VIX_MAX_POSITIONS_ELEVATED),
            ("HIGH",     cfg.VIX_ELEVATED_THRESHOLD, cfg.VIX_HIGH_THRESHOLD,      cfg.VIX_SCALE_HIGH,     cfg.VIX_MAX_POSITIONS_HIGH),
            ("CRISIS",   cfg.VIX_HIGH_THRESHOLD,     float("inf"),                cfg.VIX_SCALE_CRISIS,   cfg.VIX_MAX_POSITIONS_CRISIS),
        ]
    except Exception as exc:
        logger.warning("[VIX_REGIME] Could not import config; using hard-coded defaults: %s", exc)
        return [
            ("LOW",      0.0,  20.0,         1.00, 10),
            ("ELEVATED", 20.0, 30.0,         0.75,  7),
            ("HIGH",     30.0, 40.0,         0.50,  4),
            ("CRISIS",   40.0, float("inf"), 0.25,  2),
        ]


# ---------------------------------------------------------------------------
# Core classification
# ---------------------------------------------------------------------------

def classify_regime(vix_level: float) -> dict:
    """
    Map a VIX level to a regime dict.

    Returns
    -------
    dict with keys:
        regime         : str  — 'LOW' | 'ELEVATED' | 'HIGH' | 'CRISIS'
        vix            : float
        position_scale : float — multiply all target_weights by this
        max_positions  : int   — cap on simultaneous positions
    """
    table = _build_regime_table()
    for label, lo, hi, scale, max_n in table:
        if lo <= vix_level < hi:
            return {
                "regime":         label,
                "vix":            round(float(vix_level), 2),
                "position_scale": float(scale),
                "max_positions":  int(max_n),
            }
    # Fallback — should never be reached
    logger.error("[VIX_REGIME] VIX=%.2f fell through regime table; defaulting CRISIS", vix_level)
    return {"regime": "CRISIS", "vix": round(float(vix_level), 2), "position_scale": 0.25, "max_positions": 2}


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def _fetch_vix_yfinance(lookback_days: int = 10) -> Optional[float]:
    """
    Fetch the latest VIX close via yfinance.
    Returns None on any failure (caller provides fallback).
    """
    try:
        import yfinance as yf
        end   = date.today()
        start = end - timedelta(days=lookback_days)
        df = yf.download(
            "^VIX",
            start=start.isoformat(),
            end=end.isoformat(),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty:
            logger.warning("[VIX_REGIME] yfinance returned empty DataFrame for ^VIX")
            return None
        # Support both single-level and MultiIndex column DataFrames
        close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        val = float(close.dropna().iloc[-1])
        logger.info("[VIX_REGIME] Fetched ^VIX close = %.2f", val)
        return val
    except Exception as exc:
        logger.warning("[VIX_REGIME] yfinance fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _persist_regime(regime: dict) -> None:
    """Write regime JSON snapshot and append to CSV history."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {**regime, "as_of": date.today().isoformat()}

        # Current snapshot (overwrites)
        with open(REGIME_JSON, "w") as fh:
            json.dump(snapshot, fh, indent=2)

        # History log (append)
        write_header = not REGIME_CSV.exists()
        with open(REGIME_CSV, "a", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["as_of", "regime", "vix", "position_scale", "max_positions"],
            )
            if write_header:
                writer.writeheader()
            writer.writerow(snapshot)

    except Exception as exc:
        logger.warning("[VIX_REGIME] Could not persist regime data: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_current_regime(vix_override: Optional[float] = None) -> dict:
    """
    Fetch current VIX, classify regime, persist outputs, return regime dict.

    Parameters
    ----------
    vix_override : float, optional
        Skip yfinance and use this VIX value directly.
        Useful for backtesting, unit tests, or manual overrides.

    Returns
    -------
    dict with keys: regime, vix, position_scale, max_positions
    """
    if vix_override is not None:
        vix = float(vix_override)
        logger.info("[VIX_REGIME] Using override VIX=%.2f", vix)
    else:
        vix = _fetch_vix_yfinance()
        if vix is None:
            # Pull fallback from config; hard-code ELEVATED (25) if config unavailable
            try:
                from sleeves.sleeve_trend import config as cfg
                vix = float(cfg.VIX_FETCH_FALLBACK)
            except Exception:
                vix = 25.0
            logger.warning("[VIX_REGIME] Using fallback VIX=%.2f (fetch failed)", vix)

    regime = classify_regime(vix)
    _persist_regime(regime)

    logger.info(
        "[VIX_REGIME] Regime=%-9s  VIX=%5.2f  scale=%.2f  max_positions=%d",
        regime["regime"], regime["vix"], regime["position_scale"], regime["max_positions"],
    )
    return regime


def load_last_regime() -> Optional[dict]:
    """
    Load the most recently persisted regime from disk.
    Returns None if no snapshot exists yet.
    Useful for reporting without re-fetching VIX.
    """
    try:
        if REGIME_JSON.exists():
            with open(REGIME_JSON) as fh:
                return json.load(fh)
    except Exception as exc:
        logger.warning("[VIX_REGIME] Could not load last regime: %s", exc)
    return None


# ---------------------------------------------------------------------------
# CLI convenience
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    override = float(sys.argv[1]) if len(sys.argv) > 1 else None
    r = get_current_regime(vix_override=override)
    print(json.dumps(r, indent=2))
