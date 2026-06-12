"""Deterministic Caerus research object registry foundation."""

__all__ = ["ResearchObjectEnvelope", "SQLiteResearchRegistry"]


def __getattr__(name: str):
    if name == "ResearchObjectEnvelope":
        from research_registry.models.base import ResearchObjectEnvelope

        return ResearchObjectEnvelope
    if name == "SQLiteResearchRegistry":
        from research_registry.registry.sqlite_registry import SQLiteResearchRegistry

        return SQLiteResearchRegistry
    raise AttributeError(name)
