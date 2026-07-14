from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from paper.run_manager import safe_write_text
from paper.state_paths import get_paper_state_dir

logger = logging.getLogger(__name__)

def _default_max_position_pct() -> float:
    """Per-name cap — the SINGLE source of truth for the position-cap default.

    Explicit MAX_POSITION_PCT env always wins; otherwise the concentration
    ceiling (CAERUS_CONCENTRATED_MAX_WEIGHT, temporary pilot default 0.30). Concentration is
    ALWAYS ON (no flag), so the cap must match the concentrated book's per-name
    ceiling or the concentrated weights would be re-clipped downstream."""
    pilot_ceiling = 0.30
    explicit = os.environ.get("MAX_POSITION_PCT")
    if explicit not in (None, ""):
        try:
            return min(pilot_ceiling, float(explicit))
        except (TypeError, ValueError):
            return pilot_ceiling
    try:
        return min(
            pilot_ceiling,
            float(os.environ.get("CAERUS_CONCENTRATED_MAX_WEIGHT", str(pilot_ceiling))),
        )
    except (TypeError, ValueError):
        return pilot_ceiling


MAX_POSITION_PCT = _default_max_position_pct()
MAX_SECTOR_PCT = float(os.environ.get("MAX_SECTOR_PCT", "0.30"))

# Book layers exempt from the portfolio sector cap (Stage 1 §3.1, ratified
# 2026-07-11). The concentrated alpha book can legitimately exceed
# MAX_SECTOR_PCT (e.g. three mega-cap Tech names); for it, sector
# concentration is reviewed as a documented risk, not enforced as a hard cap.
# layer="alpha" and layer=None (unlabelled — defaults to the alpha book for
# backward compatibility, per §2) are both exempt; the diversifier and
# protection books remain subject to the cap.
SECTOR_CAP_EXEMPT_LAYERS = frozenset({"alpha", None})
MAX_NET_LONG_PCT = float(os.environ.get("MAX_NET_LONG_PCT", "0.95"))
MAX_GROSS_PCT = float(os.environ.get("MAX_GROSS_PCT", "1.00"))
CIRCUIT_BREAKER_PCT = float(os.environ.get("CIRCUIT_BREAKER_PCT", "0.15"))
CIRCUIT_BREAKER_SCALE = float(os.environ.get("CIRCUIT_BREAKER_SCALE", "0.50"))
PEAK_EQUITY_FILENAME = "peak_equity.json"


def load_sector_map(universe_path: str | Path = "data/universe.csv") -> dict[str, str]:
    path = Path(universe_path)
    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")

    df = pd.read_csv(path)
    if "ticker" not in df.columns:
        raise ValueError(f"Universe file missing ticker column: {path}")

    sector_col = "sector" if "sector" in df.columns else None
    sectors: dict[str, str] = {}
    for _, row in df.iterrows():
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        sector = str(row.get(sector_col) or "Unknown").strip() if sector_col else "Unknown"
        sectors[ticker] = sector or "Unknown"
    return sectors


def peak_equity_path(state_dir: str | Path | None = None) -> Path:
    root = Path(state_dir) if state_dir else get_paper_state_dir()
    return root / PEAK_EQUITY_FILENAME


def read_peak_equity_state(path: str | Path | None = None) -> dict[str, Any]:
    state_path = Path(path) if path else peak_equity_path()
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def update_peak_equity_state(
    *,
    current_equity: float,
    trade_date: str,
    source: str,
    path: str | Path | None = None,
) -> dict[str, Any]:
    state_path = Path(path) if path else peak_equity_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_peak_equity_state(state_path)

    existing_peak = _coerce_float(existing.get("peak_equity"), 0.0)
    peak_equity = max(existing_peak, current_equity)
    drawdown_pct = ((peak_equity - current_equity) / peak_equity) if peak_equity > 0 else 0.0

    payload = {
        "peak_equity": round(peak_equity, 4),
        "current_equity": round(current_equity, 4),
        "drawdown_pct": round(drawdown_pct, 8),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "trade_date": trade_date,
    }
    safe_write_text(
        state_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )
    return payload


@dataclass
class RiskResult:
    weights: pd.DataFrame
    cash_target_weight: float
    blocked: bool = False
    reason: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def any_adjustments(self) -> bool:
        return bool(self.actions)

    def to_artifact(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "reason": self.reason,
            "cash_target_weight": self.cash_target_weight,
            "actions": self.actions,
            "metrics": self.metrics,
            "weights": self.weights.to_dict(orient="records"),
        }


class RiskControls:
    def __init__(
        self,
        max_position_pct: float = MAX_POSITION_PCT,
        max_sector_pct: float = MAX_SECTOR_PCT,
        max_net_long_pct: float = MAX_NET_LONG_PCT,
        max_gross_pct: float = MAX_GROSS_PCT,
        circuit_breaker_pct: float = CIRCUIT_BREAKER_PCT,
        circuit_breaker_scale: float = CIRCUIT_BREAKER_SCALE,
    ):
        self.max_position_pct = max_position_pct
        self.max_sector_pct = max_sector_pct
        self.max_net_long_pct = max_net_long_pct
        self.max_gross_pct = max_gross_pct
        self.circuit_breaker_pct = circuit_breaker_pct
        self.circuit_breaker_scale = circuit_breaker_scale

    def apply_to_targets(
        self,
        targets: pd.DataFrame,
        *,
        sector_map: dict[str, str] | None = None,
        cash_target_weight: float = 0.0,
        current_equity: float | None = None,
        peak_equity: float | None = None,
        layer: str | None = None,
    ) -> RiskResult:
        # ``layer`` names the book being processed (SleeveOutput.layer). When it
        # is in SECTOR_CAP_EXEMPT_LAYERS ("alpha" or the default None, which maps
        # to the alpha book) the sector cap is skipped; diversifier/protection
        # and any other explicit layer leave the cap enforced.
        if targets is None or targets.empty:
            return RiskResult(
                weights=pd.DataFrame(columns=["ticker", "sleeve", "target_weight"]),
                cash_target_weight=1.0,
                reason="No targets supplied.",
            )

        weights = self._normalize_inputs(targets)
        actions: list[dict[str, Any]] = []
        cash_weight = _clamp(cash_target_weight, 0.0, 1.0)
        drawdown_pct = self._compute_drawdown(current_equity, peak_equity)
        circuit_triggered = drawdown_pct is not None and drawdown_pct >= self.circuit_breaker_pct

        if circuit_triggered:
            original_sum = float(weights["target_weight"].sum())
            weights["target_weight"] = weights["target_weight"] * self.circuit_breaker_scale
            new_sum = float(weights["target_weight"].sum())
            cash_weight += max(0.0, original_sum - new_sum)
            actions.append(
                {
                    "action": "circuit_breaker_scale",
                    "drawdown_pct": round(drawdown_pct, 8),
                    "threshold": self.circuit_breaker_pct,
                    "scale_factor": self.circuit_breaker_scale,
                    "cash_added": round(original_sum - new_sum, 8),
                }
            )

        weights, cash_weight, position_actions = self._apply_position_caps(weights, cash_weight)
        actions.extend(position_actions)

        sector_cap_exempt = layer in SECTOR_CAP_EXEMPT_LAYERS
        if sector_map and not sector_cap_exempt:
            weights, cash_weight, sector_actions = self._apply_sector_caps(weights, sector_map, cash_weight)
            actions.extend(sector_actions)

        weights, cash_weight, exposure_actions = self._apply_exposure_caps(weights, cash_weight)
        actions.extend(exposure_actions)

        weights = weights[weights["target_weight"] > 1e-12].copy()
        weights["target_weight"] = weights["target_weight"].round(10)
        cash_weight = round(_clamp(1.0 - float(weights["target_weight"].sum()), 0.0, 1.0), 10)

        sector_weights: dict[str, float] = {}
        if sector_map and not weights.empty:
            sector_weights = (
                weights.assign(sector=weights["ticker"].map(sector_map).fillna("Unknown"))
                .groupby("sector")["target_weight"]
                .sum()
                .round(10)
                .to_dict()
            )

        metrics = {
            "current_equity": current_equity,
            "peak_equity": peak_equity,
            "drawdown_pct": drawdown_pct,
            "circuit_breaker_triggered": circuit_triggered,
            "net_long_weight": round(float(weights["target_weight"].sum()), 10),
            "gross_weight": round(float(weights["target_weight"].abs().sum()), 10),
            "cash_target_weight": cash_weight,
            "sector_weights": sector_weights,
            "position_count": int(len(weights)),
        }
        reason = "risk controls adjusted targets" if actions else "risk controls passed"
        return RiskResult(
            weights=weights.reset_index(drop=True),
            cash_target_weight=cash_weight,
            reason=reason,
            actions=actions,
            metrics=metrics,
        )

    def check_all(
        self,
        weights: pd.DataFrame,
        sectors: dict[str, str] | None = None,
        portfolio_value: float | None = None,
        peak_value: float | None = None,
        layer: str | None = None,
    ) -> RiskResult:
        return self.apply_to_targets(
            weights,
            sector_map=sectors,
            cash_target_weight=max(0.0, 1.0 - float(weights.get("target_weight", pd.Series(dtype=float)).sum() or 0.0)),
            current_equity=portfolio_value,
            peak_equity=peak_value,
            layer=layer,
        )

    def exposure_report(
        self,
        weights: pd.DataFrame,
        sectors: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        weights = self._normalize_inputs(weights)
        report = {
            "total_weight": float(weights["target_weight"].sum()),
            "gross_weight": float(weights["target_weight"].abs().sum()),
            "n_positions": int(len(weights)),
            "max_position": float(weights["target_weight"].max()) if not weights.empty else 0.0,
            "max_position_ticker": (
                str(weights.loc[weights["target_weight"].idxmax(), "ticker"])
                if not weights.empty
                else None
            ),
            "violations": [],
        }
        for _, row in weights.iterrows():
            if float(row["target_weight"]) > self.max_position_pct:
                report["violations"].append(
                    {
                        "type": "position_cap",
                        "ticker": str(row["ticker"]),
                        "weight": float(row["target_weight"]),
                        "limit": self.max_position_pct,
                    }
                )
        if sectors and not weights.empty:
            sector_weights = (
                weights.assign(sector=weights["ticker"].map(sectors).fillna("Unknown"))
                .groupby("sector")["target_weight"]
                .sum()
            )
            report["sector_weights"] = sector_weights.to_dict()
            for sector, weight in sector_weights.items():
                if float(weight) > self.max_sector_pct:
                    report["violations"].append(
                        {
                            "type": "sector_cap",
                            "sector": str(sector),
                            "weight": float(weight),
                            "limit": self.max_sector_pct,
                        }
                    )
        return report

    def _normalize_inputs(self, targets: pd.DataFrame) -> pd.DataFrame:
        weights = targets.copy()
        if "ticker" not in weights.columns or "target_weight" not in weights.columns:
            raise ValueError("targets must contain ticker and target_weight columns")
        if "sleeve" not in weights.columns:
            weights["sleeve"] = "core"
        weights["ticker"] = weights["ticker"].astype(str).str.upper().str.strip()
        weights["sleeve"] = weights["sleeve"].astype(str).fillna("core")
        weights["target_weight"] = pd.to_numeric(weights["target_weight"], errors="coerce").fillna(0.0)
        weights = weights[weights["ticker"] != "CASH"].copy()
        weights = weights[weights["target_weight"] > 0.0].copy()
        return weights[["ticker", "sleeve", "target_weight"]].reset_index(drop=True)

    def _apply_position_caps(
        self,
        weights: pd.DataFrame,
        cash_weight: float,
    ) -> tuple[pd.DataFrame, float, list[dict[str, Any]]]:
        actions: list[dict[str, Any]] = []
        for idx in weights.index[weights["target_weight"] > self.max_position_pct]:
            before = float(weights.loc[idx, "target_weight"])
            after = self.max_position_pct
            cash_weight += before - after
            weights.loc[idx, "target_weight"] = after
            actions.append(
                {
                    "action": "position_cap",
                    "ticker": str(weights.loc[idx, "ticker"]),
                    "before": round(before, 10),
                    "after": round(after, 10),
                    "limit": self.max_position_pct,
                }
            )
        return weights, cash_weight, actions

    def _apply_sector_caps(
        self,
        weights: pd.DataFrame,
        sector_map: dict[str, str],
        cash_weight: float,
    ) -> tuple[pd.DataFrame, float, list[dict[str, Any]]]:
        actions: list[dict[str, Any]] = []
        weights = weights.copy()
        weights["_sector"] = weights["ticker"].map(sector_map).fillna("Unknown")

        for sector, group in weights.groupby("_sector"):
            sector_weight = float(group["target_weight"].sum())
            if sector_weight <= self.max_sector_pct + 1e-12:
                continue
            scale = self.max_sector_pct / sector_weight
            for idx in group.index:
                before = float(weights.loc[idx, "target_weight"])
                after = before * scale
                weights.loc[idx, "target_weight"] = after
                actions.append(
                    {
                        "action": "sector_cap",
                        "ticker": str(weights.loc[idx, "ticker"]),
                        "sector": str(sector),
                        "before": round(before, 10),
                        "after": round(after, 10),
                        "sector_weight_before": round(sector_weight, 10),
                        "sector_limit": self.max_sector_pct,
                    }
                )
            cash_weight += sector_weight - self.max_sector_pct

        weights = weights.drop(columns="_sector")
        return weights, cash_weight, actions

    def _apply_exposure_caps(
        self,
        weights: pd.DataFrame,
        cash_weight: float,
    ) -> tuple[pd.DataFrame, float, list[dict[str, Any]]]:
        net_long = float(weights["target_weight"].sum())
        gross = float(weights["target_weight"].abs().sum())
        allowed = min(self.max_net_long_pct, self.max_gross_pct)
        if max(net_long, gross) <= allowed + 1e-12:
            return weights, cash_weight, []

        scale = allowed / max(net_long, gross)
        before = net_long
        weights = weights.copy()
        weights["target_weight"] = weights["target_weight"] * scale
        after = float(weights["target_weight"].sum())
        cash_weight += before - after
        return (
            weights,
            cash_weight,
            [
                {
                    "action": "exposure_cap",
                    "before_net_long": round(net_long, 10),
                    "before_gross": round(gross, 10),
                    "after_net_long": round(after, 10),
                    "after_gross": round(float(weights["target_weight"].abs().sum()), 10),
                    "net_limit": self.max_net_long_pct,
                    "gross_limit": self.max_gross_pct,
                    "scale_factor": round(scale, 10),
                }
            ],
        )

    def _compute_drawdown(
        self,
        current_equity: float | None,
        peak_equity: float | None,
    ) -> float | None:
        if current_equity is None or peak_equity is None or peak_equity <= 0:
            return None
        return max(0.0, (peak_equity - current_equity) / peak_equity)


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
