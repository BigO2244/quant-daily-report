"""Transparent deterministic priority scoring with stable ordering."""

from __future__ import annotations

from typing import Any

from .store import AegisStore

DEFAULT_WEIGHTS = {"urgency": 1.0, "importance": 1.0, "risk": 1.0, "readiness": 1.0}


class PriorityEngine:
    def __init__(self, store: AegisStore, weights: dict[str, float] | None = None) -> None:
        self.store = store; self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    def score(self, mission_id: str, inputs: dict[str, float], as_of: str) -> dict[str, Any]:
        required = {"criticality", "blocker_impact", "dependency_count", "decision_urgency", "production_risk", "research_value", "data_readiness", "effort_remaining", "age", "executive_priority", "required_by_active", "incident_resolution", "evidence_readiness"}
        unknown = set(inputs) - required
        if unknown: raise ValueError(f"Unknown priority inputs: {sorted(unknown)}")
        values = {key: float(inputs.get(key, 0.0)) for key in required}
        if any(value < 0 or value > 5 for value in values.values()): raise ValueError("Priority inputs must be between 0 and 5")
        urgency = values["decision_urgency"] + values["age"] + values["executive_priority"]
        importance = values["criticality"] + values["research_value"] + values["required_by_active"] + values["dependency_count"]
        risk = values["production_risk"] + values["blocker_impact"] + values["incident_resolution"]
        readiness = values["data_readiness"] + values["evidence_readiness"] + (5.0 - values["effort_remaining"])
        components = {"urgency": urgency, "importance": importance, "risk": risk, "readiness": readiness}
        total = sum(components[name] * self.weights[name] for name in components)
        result = {"mission_id": mission_id, **components, "total": total,
                  "explanation": {"inputs": values, "weights": self.weights, "formula": "weighted sum of explicit urgency, importance, risk, and readiness components"}}
        self.store.save_priority(result, as_of)
        return result

    def ranking(self) -> list[dict[str, Any]]:
        scores = self.store.priorities(); overrides = {item["mission_id"]: item for item in self.store.overrides()}
        for score in scores: score["override"] = overrides.get(score["mission_id"])
        return sorted(scores, key=lambda item: (item["override"]["override_rank"] if item["override"] else 10**9, -item["total"], item["mission_id"]))
