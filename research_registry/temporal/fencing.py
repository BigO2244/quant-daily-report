"""SEM-006 temporal fencing and as-of admissibility."""

from __future__ import annotations

from datetime import datetime, timezone

from research_registry.models.base import ResearchObjectEnvelope


def parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"timestamp is not UTC: {value}")
    return parsed


class TemporalFence:
    def assert_anchor(self, anchor: str) -> datetime:
        parsed = parse_utc(anchor)
        if parsed > datetime.now(timezone.utc):
            raise ValueError("T_anchor must not be in the future")
        return parsed

    def admissible(self, envelope: ResearchObjectEnvelope, anchor: str) -> bool:
        anchor_dt = self.assert_anchor(anchor)
        as_of = parse_utc(envelope.temporal["as_of"])
        produced_at = parse_utc(envelope.provenance["produced_at"])
        return as_of <= anchor_dt and produced_at <= anchor_dt

    def fence(self, envelopes: list[ResearchObjectEnvelope], anchor: str) -> list[ResearchObjectEnvelope]:
        return [envelope for envelope in envelopes if self.admissible(envelope, anchor)]
