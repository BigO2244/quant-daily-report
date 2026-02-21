# engine/breaker.py
import os
import numpy as np

def get_breaker_config() -> dict:
    def _float_env(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return float(str(raw).strip())
        except Exception:
            return default

    mode_raw = str(os.getenv("BREAKER_MODE", "partial")).strip().lower()
    mode = mode_raw if mode_raw in {"off", "lock", "partial"} else "partial"

    partial_exposure = max(0.0, min(1.0, _float_env("BREAKER_PARTIAL_EXPOSURE", 0.5)))

    if mode == "off":
        exposure_multiplier = 1.0
        label = "OFF"
    elif mode == "lock":
        exposure_multiplier = 0.0
        label = "LOCK"
    else:
        exposure_multiplier = partial_exposure
        label = "PARTIAL"

    return {
        "mode": mode,
        "exposure_multiplier": exposure_multiplier,
        "label": label,
        # Backward-compatible fields for existing helper paths.
        "partial_exposure": partial_exposure,
        "lock_exposure": 0.0,
        "reentry_ramp": [0.25, 0.5, 1.0],
        "off_days_for_full": 1,
        "max_consec_lock_days": 10,
    }


def compute_exposure_multiplier(
    breaker_any: np.ndarray,
    breaker_lock: np.ndarray | None = None,
    *,
    cfg: dict | None = None,
) -> np.ndarray:
    """
    Returns exposure multiplier per day, in [0, 1].
    breaker_any: bool array (breaker active days)
    breaker_lock: optional bool array (subset)
    """
    if cfg is None:
        cfg = get_breaker_config()

    n = len(breaker_any)
    if breaker_lock is None:
        breaker_lock = np.zeros(n, dtype=bool)

    mode = cfg["mode"]
    partial = float(cfg["partial_exposure"])
    lock_exp = float(cfg["lock_exposure"])
    ramp = list(cfg["reentry_ramp"])
    off_days_for_full = int(cfg["off_days_for_full"])
    max_consec_lock = int(cfg["max_consec_lock_days"])

    exp = np.ones(n, dtype=float)

    if mode == "off":
        return exp

    if mode == "lock":
        exp[:] = lock_exp
        return exp

    if mode == "partial":
        exp[breaker_any] = partial
        exp[breaker_lock] = lock_exp
        return exp

    if mode == "full_lock":
        exp[breaker_any] = lock_exp
        return exp

    if mode == "full_lock_ramp":
        exp[breaker_any] = lock_exp

        ramp_idx = 0
        off_streak = 0
        consec_lock = 0

        for t in range(n):
            if breaker_any[t]:
                consec_lock += 1
                off_streak = 0
                ramp_idx = 0

                # guardrail: if locked too long, allow small exposure
                if max_consec_lock > 0 and consec_lock > max_consec_lock:
                    exp[t] = max(exp[t], ramp[0] if ramp else 0.25)
            else:
                consec_lock = 0
                off_streak += 1

                if off_streak < off_days_for_full:
                    exp[t] = min(exp[t], ramp[0] if ramp else 0.25)
                else:
                    if ramp_idx < len(ramp):
                        exp[t] = ramp[ramp_idx]
                        ramp_idx += 1
                    else:
                        exp[t] = 1.0

        return exp

    # safe fallback
    exp[breaker_any] = partial
    exp[breaker_lock] = lock_exp
    return exp

def apply_portfolio_exposure_overlay(weights_df, exposure_multiplier: float, cash_ticker: str = "CASH"):
    """
    Scale non-cash weights by exposure_multiplier and put the remainder into CASH.
    Expects weights_df columns: ticker, target_weight (and optional reason/sleeve_name).
    """
    import pandas as pd

    if weights_df is None or weights_df.empty:
        return weights_df

    m = float(max(0.0, min(1.0, float(exposure_multiplier))))
    df = weights_df.copy()

    if "ticker" not in df.columns or "target_weight" not in df.columns:
        return df

    df["ticker"] = df["ticker"].astype(str)
    df["target_weight"] = df["target_weight"].astype(float)

    mask_cash = df["ticker"] == cash_ticker

    # Scale everything except cash
    df.loc[~mask_cash, "target_weight"] = df.loc[~mask_cash, "target_weight"] * m

    total = float(df["target_weight"].sum())
    residual = max(0.0, 1.0 - total)

    if mask_cash.any():
        df.loc[mask_cash, "target_weight"] = df.loc[mask_cash, "target_weight"] + residual
    else:
        cash_row = {"ticker": cash_ticker, "target_weight": residual}
        # preserve optional columns if present
        if "reason" in df.columns:
            cash_row["reason"] = "BREAKER_OVERLAY"
        if "sleeve_name" in df.columns:
            cash_row["sleeve_name"] = cash_ticker
        df = pd.concat([df, pd.DataFrame([cash_row])], ignore_index=True)

    # tidy: drop near-zero rows
    df = df[df["target_weight"].abs() > 1e-12].copy()
    return df
