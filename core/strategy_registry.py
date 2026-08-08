from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "caerus_strategy_registry_v1"

VALID_STATUSES = {"paper", "shadow", "research", "retired"}
VALID_STRATEGY_TYPES = {"security_selection", "overlay", "meta_model", "benchmark", "reference_portfolio"}
VALID_FAMILIES = {
    "core_momentum",
    "crisis_reversal",
    "earnings_drift",
    "event_driven",
    "regime_overlay",
    "benchmark",
    "reference",
}
VALID_EXECUTION_IMPACTS = {"NON_EXECUTIONAL", "PAPER", "LIVE"}

REQUIRED_FIELDS = {
    "strategy_id",
    "display_name",
    "strategy_type",
    "family",
    "status",
    "eligible_for_shadow",
    "eligible_for_promotion",
    "benchmark",
    "execution_impact",
}


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "research" / "strategy_registry.json"


def registry_path_for_repo(repo_root: Path | str) -> Path:
    candidate = Path(repo_root) / "config" / "research" / "strategy_registry.json"
    return candidate if candidate.exists() else default_registry_path()


@dataclass(frozen=True)
class StrategyRegistryEntry:
    strategy_id: str
    display_name: str
    strategy_type: str
    family: str
    status: str
    eligible_for_shadow: bool
    eligible_for_promotion: bool
    benchmark: str | None
    execution_impact: str
    short_name: str | None = None
    role: str | None = None
    display_order: int = 1000
    capabilities: dict[str, bool] | None = None
    shadow_tracking: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "StrategyRegistryEntry":
        missing = sorted(REQUIRED_FIELDS - set(payload))
        if missing:
            raise ValueError(f"strategy registry entry missing required fields: {', '.join(missing)}")

        entry = cls(
            strategy_id=str(payload["strategy_id"]).strip(),
            display_name=str(payload["display_name"]).strip(),
            strategy_type=str(payload["strategy_type"]).strip(),
            family=str(payload["family"]).strip(),
            status=str(payload["status"]).strip(),
            eligible_for_shadow=bool(payload["eligible_for_shadow"]),
            eligible_for_promotion=bool(payload["eligible_for_promotion"]),
            benchmark=str(payload["benchmark"]).strip() if payload.get("benchmark") is not None else None,
            execution_impact=str(payload["execution_impact"]).strip(),
            short_name=str(payload.get("short_name") or "").strip() or None,
            role=str(payload.get("role") or "").strip() or None,
            display_order=int(payload.get("display_order") or 1000),
            capabilities=dict(payload.get("capabilities") or {}),
            shadow_tracking=dict(payload.get("shadow_tracking") or {}),
            raw=dict(payload),
        )
        entry.validate()
        return entry

    def validate(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy registry entry has blank strategy_id")
        if not self.display_name:
            raise ValueError(f"{self.strategy_id}: display_name is required")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"{self.strategy_id}: invalid status {self.status!r}")
        if self.strategy_type not in VALID_STRATEGY_TYPES:
            raise ValueError(f"{self.strategy_id}: invalid strategy_type {self.strategy_type!r}")
        if self.family not in VALID_FAMILIES:
            raise ValueError(f"{self.strategy_id}: invalid family {self.family!r}")
        if self.execution_impact not in VALID_EXECUTION_IMPACTS:
            raise ValueError(f"{self.strategy_id}: invalid execution_impact {self.execution_impact!r}")
        if self.strategy_type in {"overlay", "meta_model"}:
            capabilities = self.capabilities or {}
            if capabilities.get("produces_holdings") or capabilities.get("produces_nav"):
                raise ValueError(f"{self.strategy_id}: {self.strategy_type} strategies must not declare holdings or NAV capability")
        if self.strategy_type != "security_selection" and self.eligible_for_promotion:
            raise ValueError(f"{self.strategy_id}: only security_selection strategies may be promotion-eligible")

    @property
    def active_in_shadow_tracking(self) -> bool:
        tracking = self.shadow_tracking or {}
        return (
            self.strategy_type == "security_selection"
            and self.status in {"paper", "shadow"}
            and self.eligible_for_shadow
            and bool(tracking.get("enabled"))
        )

    @property
    def is_security_selection(self) -> bool:
        return self.strategy_type == "security_selection"

    @property
    def is_overlay(self) -> bool:
        return self.strategy_type == "overlay"

    @property
    def is_meta_model(self) -> bool:
        return self.strategy_type == "meta_model"

    @property
    def promotion_candidate(self) -> bool:
        return (
            self.strategy_type == "security_selection"
            and self.status == "shadow"
            and self.eligible_for_promotion
        )

    def label(self) -> str:
        return self.display_name

    def compact_name(self) -> str:
        return self.short_name or self.strategy_id.replace("caerus_", "")


class StrategyRegistry:
    def __init__(self, entries: Iterable[StrategyRegistryEntry]) -> None:
        self.entries = tuple(sorted(entries, key=lambda item: (item.display_order, item.strategy_id)))
        self._by_id = {entry.strategy_id: entry for entry in self.entries}
        if len(self._by_id) != len(self.entries):
            raise ValueError("strategy registry contains duplicate strategy_id values")

    @classmethod
    def from_path(cls, path: Path | str | None = None) -> "StrategyRegistry":
        registry_path = Path(path) if path is not None else default_registry_path()
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported strategy registry schema_version: {payload.get('schema_version')!r}")
        raw_entries = payload.get("strategies")
        if not isinstance(raw_entries, list):
            raise ValueError("strategy registry must contain a strategies list")
        return cls(StrategyRegistryEntry.from_payload(item) for item in raw_entries)

    def get(self, strategy_id: str) -> StrategyRegistryEntry | None:
        return self._by_id.get(strategy_id)

    def require(self, strategy_id: str) -> StrategyRegistryEntry:
        entry = self.get(strategy_id)
        if entry is None:
            raise KeyError(f"unknown strategy_id: {strategy_id}")
        return entry

    def active_shadow_security_selection_entries(self) -> tuple[StrategyRegistryEntry, ...]:
        return tuple(entry for entry in self.entries if entry.active_in_shadow_tracking)

    def active_shadow_security_selection_ids(self) -> tuple[str, ...]:
        return tuple(entry.strategy_id for entry in self.active_shadow_security_selection_entries())

    def security_selection_entries(self) -> tuple[StrategyRegistryEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_security_selection)

    def overlay_entries(self) -> tuple[StrategyRegistryEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_overlay)

    def promotion_candidate_ids(self) -> tuple[str, ...]:
        return tuple(entry.strategy_id for entry in self.entries if entry.promotion_candidate)

    def research_challenger_ids(self) -> tuple[str, ...]:
        """Strategies compared with the research baseline, including PAPER names."""
        return tuple(
            entry.strategy_id
            for entry in self.active_shadow_security_selection_entries()
            if entry.role == "challenger"
        )

    def paper_execution_entries(self) -> tuple[StrategyRegistryEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.status == "paper"
            and entry.execution_impact == "PAPER"
            and bool((entry.raw or {}).get("paper_execution", {}).get("enabled"))
        )

    def paper_execution_strategy_id(self) -> str:
        entries = self.paper_execution_entries()
        if len(entries) != 1:
            raise ValueError(
                "strategy registry must contain exactly one enabled PAPER execution strategy"
            )
        return entries[0].strategy_id

    def paper_execution_config(self) -> dict[str, Any]:
        entry = self.require(self.paper_execution_strategy_id())
        return dict((entry.raw or {}).get("paper_execution") or {})

    def baseline_strategy_id(self) -> str:
        baselines = [
            entry.strategy_id
            for entry in self.active_shadow_security_selection_entries()
            if entry.role == "baseline" or entry.status == "paper"
        ]
        if not baselines:
            raise ValueError("strategy registry has no active baseline strategy")
        return baselines[0]

    def strategy_labels(self) -> dict[str, str]:
        return {entry.strategy_id: entry.display_name for entry in self.entries}

    def strategy_short_names(self) -> dict[str, str]:
        return {entry.strategy_id: entry.compact_name() for entry in self.entries}


def load_strategy_registry(path: Path | str | None = None) -> StrategyRegistry:
    return StrategyRegistry.from_path(path)


def load_strategy_registry_for_repo(repo_root: Path | str) -> StrategyRegistry:
    return load_strategy_registry(registry_path_for_repo(repo_root))


def active_shadow_security_selection_ids(path: Path | str | None = None) -> tuple[str, ...]:
    return load_strategy_registry(path).active_shadow_security_selection_ids()
