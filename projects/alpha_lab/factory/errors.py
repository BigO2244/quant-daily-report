"""Fail-closed error types for the research factory."""


class ContractValidationError(ValueError):
    """A typed research contract is incomplete or internally inconsistent."""


class ProviderNotReadyError(ContractValidationError):
    """A required provider or dataset has not passed its readiness gate."""


class EventStoreIntegrityError(RuntimeError):
    """An append-only research event log failed integrity validation."""


class ResearchBoundaryError(ValueError):
    """A requested path crosses the Alpha Lab research-only boundary."""
