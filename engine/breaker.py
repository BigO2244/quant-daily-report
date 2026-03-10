# engine/breaker.py
import logging
import os
import numpy as np

logger = logging.getLogger(__name__)

def get_breaker_config() -> dict:
    def _float_env(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return float(str(raw).strip())
        except Exception:
            return default

    raw_mode_env = os.getenv("BREAKER_MODE")
    if raw_mode_env is None:
        # No explicit override — default to OFF so the strategy runs at full
        # exposure unless a real trigger is configured.
        mode = "off"
        source = "default"
        reason = "no BREAKER_MODE env var set; defaulting to OFF / full exposure"
    else:
        mode_raw = str(raw_mode_env).strip().lower()
        if mode_raw in {"off", "lock", "partial"}:
            mode = mode_raw
            source = "env"
            reason = f"BREAKER_MODE={mode_raw}"
        else:
            mode = "off"
            source = "default"
            reason = f"invalid BREAKER_MODE value '{mode_raw}'; defaulting to OFF / full exposure"

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

    logger.debug(
        "[BREAKER_CONFIG] mode=%s source=%s reason=%s exposure=%.4f",
        mode, source, reason, exposure_multiplier,
    )

    return {
        "mode": mode,
        "exposure_multiplier": exposure_multiplier,
        "label": label,
        "source": source,
        "reason": reason,
        # Backward-compatible fields for existing helper paths.
        "partial_exposure": partial_exposure,
        "lock_exposure": 0.0,
        "reentry_ramp": [0.25, 0.5, 1.0],
        "off_days_for_full": 1,
        "max_consec_lock_days": 10,
    }


def get_exposure_multiplier(
    breaker_policy: str,
    breaker_state: dict | None = None,
    *,
    state_can_override: bool | None = None,
) -> float:
    """
    Resolve a single effective exposure multiplier from policy + state.

    Precedence:
      1) BREAKER_POLICY (or breaker_policy arg) sets default multiplier.
      2) breaker_state may override ONLY when BREAKER_STATE_CAN_OVERRIDE=1
         (or state_can_override=True explicitly).
    """
    policy = str(
        breaker_policy or os.getenv("BREAKER_POLICY", "FULL")
    ).strip().upper()
    if policy not in {"FULL", "PARTIAL", "LOCK"}:
        policy = "FULL"

    state = dict(breaker_state or {})
    mode = str(state.get("mode", "")).strip().lower()
    if state_can_override is None:
        raw_override = os.getenv("BREAKER_STATE_CAN_OVERRIDE", "1")
        override_allowed = str(raw_override).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
    else:
        override_allowed = bool(state_can_override)

    explicit_mult = None
    for key in (
        "exposure_multiplier",
        "exposure_multiplier_today",
        "exposure",
        "multiplier",
    ):
        if key in state:
            try:
                explicit_mult = float(state[key])
                break
            except Exception:
                explicit_mult = None

    partial_default = 0.5
    try:
        partial_default = float(
            state.get(
                "partial_exposure",
                os.getenv("BREAKER_PARTIAL_EXPOSURE", "0.5"),
            )
        )
    except Exception:
        partial_default = 0.5
    partial_default = max(0.0, min(1.0, partial_default))

    default_mult = {"FULL": 1.0, "PARTIAL": partial_default, "LOCK": 0.0}[policy]
    mult = default_mult

    if override_allowed:
        if mode == "lock":
            mult = 0.0
        elif mode == "partial":
            mult = (
                float(explicit_mult)
                if explicit_mult is not None
                else float(partial_default)
            )
        elif mode in {"off", "full", "unlock"}:
            mult = float(explicit_mult) if explicit_mult is not None else 1.0
        elif explicit_mult is not None:
            mult = float(explicit_mult)

    mult = max(0.0, min(1.0, float(mult)))
    logger.debug(
        "[BREAKER_POLICY] env=%s state_mode=%s override_allowed=%d multiplier=%.4f",
        policy, mode or "none", 1 if override_allowed else 0, mult,
    )
    return mult


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
