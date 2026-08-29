"""Configuration contract for the forward options proxy lane."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from projects.alpha_lab.factory import ContractValidationError, canonical_hash


@dataclass(frozen=True)
class ProxyConfig:
    schema_version: str
    hypothesis_id: str
    experiment_relationship: str
    source: str
    automation_scope: str
    production_scheduler_integration: bool
    benchmark_symbol: str
    sector_classification: str
    sector_by_symbol: Mapping[str, str]
    symbols: Tuple[str, ...]
    decision_timezone: str
    decision_not_before: str
    minimum_dte: int
    maximum_dte: int
    minimum_absolute_delta: float
    maximum_absolute_delta: float
    minimum_valid_contracts: int
    minimum_nonzero_volume_contracts: int
    minimum_source_coverage: float
    symbol_pause_seconds: float
    maximum_symbol_attempts: int
    retry_backoff_seconds: float
    top_fraction: float
    maximum_positions: int
    maximum_position_weight: float
    holding_period_trading_days: int
    base_cost_bps_per_side: float
    stress_cost_bps_per_side: float
    review_checkpoints_observations: Tuple[int, ...]
    risk_free_rate_assumption: float
    dividend_yield_assumption: float

    def __post_init__(self) -> None:
        if self.schema_version != "caerus_options_proxy_config_v1":
            raise ContractValidationError("unsupported options proxy config schema")
        if self.hypothesis_id != "HYP-2026-004":
            raise ContractValidationError("proxy infrastructure must remain linked to HYP-2026-004")
        if self.experiment_relationship != (
            "FORWARD_PROXY_ONLY_DOES_NOT_SATISFY_FROZEN_DATA_CONTRACT"
        ):
            raise ContractValidationError("proxy evidence relationship cannot be promoted")
        if self.source != "yfinance_current_chain":
            raise ContractValidationError("only the declared current-chain proxy source is allowed")
        if self.automation_scope != "standalone_research_only":
            raise ContractValidationError("options proxy automation must remain standalone research-only")
        if self.production_scheduler_integration is not False:
            raise ContractValidationError("production scheduler integration is forbidden")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ContractValidationError("symbols must be non-empty and unique")
        if self.benchmark_symbol not in self.symbols:
            raise ContractValidationError("benchmark_symbol must be present in symbols")
        if any(not symbol or symbol != symbol.upper() for symbol in self.symbols):
            raise ContractValidationError("symbols must be non-empty uppercase strings")
        if not self.sector_classification:
            raise ContractValidationError("sector_classification is required")
        if set(self.sector_by_symbol) != set(self.symbols):
            raise ContractValidationError("sector_by_symbol must cover every configured symbol")
        if any(not str(value).strip() for value in self.sector_by_symbol.values()):
            raise ContractValidationError("sector labels must be non-empty")
        object.__setattr__(
            self,
            "sector_by_symbol",
            MappingProxyType(dict(self.sector_by_symbol)),
        )
        try:
            ZoneInfo(self.decision_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ContractValidationError("decision_timezone is unavailable") from exc
        hour, minute = _parse_clock(self.decision_not_before)
        if hour < 9 or (hour == 9 and minute < 30) or hour > 16:
            raise ContractValidationError("decision_not_before must be within the regular session")
        if not (0 <= self.minimum_dte <= self.maximum_dte):
            raise ContractValidationError("invalid DTE bounds")
        if not (
            0 < self.minimum_absolute_delta < self.maximum_absolute_delta <= 1
        ):
            raise ContractValidationError("invalid absolute-delta bounds")
        if self.minimum_valid_contracts < 1 or self.minimum_nonzero_volume_contracts < 1:
            raise ContractValidationError("contract coverage thresholds must be positive")
        if not 0 < self.minimum_source_coverage <= 1:
            raise ContractValidationError("minimum_source_coverage must be in (0, 1]")
        if not math.isfinite(self.symbol_pause_seconds) or not 0 <= self.symbol_pause_seconds <= 10:
            raise ContractValidationError("symbol_pause_seconds must be in [0, 10]")
        if not 1 <= self.maximum_symbol_attempts <= 5:
            raise ContractValidationError("maximum_symbol_attempts must be in [1, 5]")
        if not math.isfinite(self.retry_backoff_seconds) or not 0 <= self.retry_backoff_seconds <= 30:
            raise ContractValidationError("retry_backoff_seconds must be in [0, 30]")
        if not 0 < self.top_fraction <= 1:
            raise ContractValidationError("top_fraction must be in (0, 1]")
        if self.maximum_positions < 1:
            raise ContractValidationError("maximum_positions must be positive")
        if not 0 < self.maximum_position_weight <= 1:
            raise ContractValidationError("maximum_position_weight must be in (0, 1]")
        if self.holding_period_trading_days < 1:
            raise ContractValidationError("holding period must be positive")
        for field_name in (
            "base_cost_bps_per_side",
            "stress_cost_bps_per_side",
            "risk_free_rate_assumption",
            "dividend_yield_assumption",
        ):
            value = getattr(self, field_name)
            if not math.isfinite(value):
                raise ContractValidationError("{} must be finite".format(field_name))
        if self.base_cost_bps_per_side < 0:
            raise ContractValidationError("base costs cannot be negative")
        if self.stress_cost_bps_per_side < self.base_cost_bps_per_side:
            raise ContractValidationError("stress costs cannot be below base costs")
        if not self.review_checkpoints_observations or any(
            value < 1 for value in self.review_checkpoints_observations
        ):
            raise ContractValidationError("review checkpoints must be positive")

    @property
    def config_hash(self) -> str:
        return canonical_hash(self.to_dict())

    @property
    def candidate_symbols(self) -> Tuple[str, ...]:
        return tuple(symbol for symbol in self.symbols if symbol != self.benchmark_symbol)

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis_id": self.hypothesis_id,
            "experiment_relationship": self.experiment_relationship,
            "source": self.source,
            "automation_scope": self.automation_scope,
            "production_scheduler_integration": self.production_scheduler_integration,
            "benchmark_symbol": self.benchmark_symbol,
            "sector_classification": self.sector_classification,
            "sector_by_symbol": self.sector_by_symbol,
            "symbols": self.symbols,
            "decision_timezone": self.decision_timezone,
            "decision_not_before": self.decision_not_before,
            "minimum_dte": self.minimum_dte,
            "maximum_dte": self.maximum_dte,
            "minimum_absolute_delta": self.minimum_absolute_delta,
            "maximum_absolute_delta": self.maximum_absolute_delta,
            "minimum_valid_contracts": self.minimum_valid_contracts,
            "minimum_nonzero_volume_contracts": self.minimum_nonzero_volume_contracts,
            "minimum_source_coverage": self.minimum_source_coverage,
            "symbol_pause_seconds": self.symbol_pause_seconds,
            "maximum_symbol_attempts": self.maximum_symbol_attempts,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "top_fraction": self.top_fraction,
            "maximum_positions": self.maximum_positions,
            "maximum_position_weight": self.maximum_position_weight,
            "holding_period_trading_days": self.holding_period_trading_days,
            "base_cost_bps_per_side": self.base_cost_bps_per_side,
            "stress_cost_bps_per_side": self.stress_cost_bps_per_side,
            "review_checkpoints_observations": self.review_checkpoints_observations,
            "risk_free_rate_assumption": self.risk_free_rate_assumption,
            "dividend_yield_assumption": self.dividend_yield_assumption,
        }


def _parse_clock(value: str) -> Tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ContractValidationError("decision_not_before must be HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ContractValidationError("decision_not_before must be HH:MM")
    return hour, minute


def load_config(path: Path) -> ProxyConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ContractValidationError("options proxy config must be a JSON object")
    return ProxyConfig(
        schema_version=raw["schema_version"],
        hypothesis_id=raw["hypothesis_id"],
        experiment_relationship=raw["experiment_relationship"],
        source=raw["source"],
        automation_scope=raw["automation_scope"],
        production_scheduler_integration=raw["production_scheduler_integration"],
        benchmark_symbol=raw["benchmark_symbol"],
        sector_classification=raw["sector_classification"],
        sector_by_symbol=raw["sector_by_symbol"],
        symbols=tuple(raw["symbols"]),
        decision_timezone=raw["decision_timezone"],
        decision_not_before=raw["decision_not_before"],
        minimum_dte=int(raw["minimum_dte"]),
        maximum_dte=int(raw["maximum_dte"]),
        minimum_absolute_delta=float(raw["minimum_absolute_delta"]),
        maximum_absolute_delta=float(raw["maximum_absolute_delta"]),
        minimum_valid_contracts=int(raw["minimum_valid_contracts"]),
        minimum_nonzero_volume_contracts=int(raw["minimum_nonzero_volume_contracts"]),
        minimum_source_coverage=float(raw["minimum_source_coverage"]),
        symbol_pause_seconds=float(raw["symbol_pause_seconds"]),
        maximum_symbol_attempts=int(raw["maximum_symbol_attempts"]),
        retry_backoff_seconds=float(raw["retry_backoff_seconds"]),
        top_fraction=float(raw["top_fraction"]),
        maximum_positions=int(raw["maximum_positions"]),
        maximum_position_weight=float(raw["maximum_position_weight"]),
        holding_period_trading_days=int(raw["holding_period_trading_days"]),
        base_cost_bps_per_side=float(raw["base_cost_bps_per_side"]),
        stress_cost_bps_per_side=float(raw["stress_cost_bps_per_side"]),
        review_checkpoints_observations=tuple(
            int(value) for value in raw["review_checkpoints_observations"]
        ),
        risk_free_rate_assumption=float(raw["risk_free_rate_assumption"]),
        dividend_yield_assumption=float(raw["dividend_yield_assumption"]),
    )


def default_config_path() -> Path:
    return Path(__file__).with_name("config.json")
