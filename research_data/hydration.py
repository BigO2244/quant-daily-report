from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


STATUS_VALUES = {
    "OK",
    "PARTIAL",
    "BLOCKED_SUBSCRIPTION",
    "BLOCKED_CREDENTIALS",
    "BLOCKED_AUTH_OR_ENTITLEMENT",
    "BLOCKED_ACCOUNT_REQUIRED",
    "RATE_LIMITED",
    "SOURCE_UNAVAILABLE",
    "SCHEMA_ERROR",
    "EMPTY_RESULT",
    "FAILED_UNKNOWN",
}
SUCCESS_STATUSES = {"OK", "PARTIAL"}


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_as_of_date() -> str:
    return date.today().isoformat()


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def env_present(*names: str) -> bool:
    return all(bool(os.environ.get(name)) for name in names)


def env_any(*names: str) -> bool:
    return any(bool(os.environ.get(name)) for name in names)


def redact_env_name(name: str) -> str:
    if not name:
        return ""
    return name.upper()


@dataclass(frozen=True)
class HydrationContext:
    repo_root: Path
    as_of_date: str
    dry_run: bool = False
    limit_sample: bool = False
    timeout_seconds: int = 12
    user_agent: str = "CaerusDataHydration/0.1 research-data-contact@example.invalid"

    def output_path(self, area: str, dataset_id: str, source_name: str, filename: str) -> Path:
        clean_source = source_name.replace("/", "_").replace(" ", "_")
        return self.repo_root / "data" / area / dataset_id / clean_source / filename


@dataclass
class HydrationResult:
    dataset_id: str
    dataset_name: str
    fr_dh_reference: str
    source_attempted: str
    source_type: str
    status: str
    failure_reason: str
    recommended_user_action: str
    records_written: int
    artifact_path: str | None
    started_at: str
    completed_at: str
    ingestion_timestamp: str
    as_of_date: str
    effective_date_available: bool
    filing_date_available: bool
    PIT_safe_status: str
    validation_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseHydrationAdapter:
    source_name = "base"
    source_type = "unknown"

    def supports(self, dataset_id: str) -> bool:
        raise NotImplementedError

    def hydrate(self, dataset: dict[str, Any], context: HydrationContext) -> HydrationResult:
        raise NotImplementedError

    def result(
        self,
        dataset: dict[str, Any],
        context: HydrationContext,
        *,
        status: str,
        started_at: str,
        failure_reason: str = "",
        recommended_user_action: str = "",
        records_written: int = 0,
        artifact_path: Path | str | None = None,
        effective_date_available: bool = False,
        filing_date_available: bool = False,
        pit_safe_status: str | None = None,
        validation_status: str | None = None,
    ) -> HydrationResult:
        if status not in STATUS_VALUES:
            raise ValueError(f"Unsupported hydration status: {status}")
        completed_at = utc_now_iso()
        artifact = str(artifact_path) if artifact_path else None
        return HydrationResult(
            dataset_id=str(dataset["dataset_id"]),
            dataset_name=str(dataset["dataset_name"]),
            fr_dh_reference=str(dataset.get("fr_dh_reference") or "FR-DH-013"),
            source_attempted=self.source_name,
            source_type=self.source_type,
            status=status,
            failure_reason=failure_reason,
            recommended_user_action=recommended_user_action,
            records_written=int(records_written or 0),
            artifact_path=artifact,
            started_at=started_at,
            completed_at=completed_at,
            ingestion_timestamp=completed_at,
            as_of_date=context.as_of_date,
            effective_date_available=bool(effective_date_available),
            filing_date_available=bool(filing_date_available),
            PIT_safe_status=pit_safe_status or ("PIT_SAFE_UNVERIFIED" if status in SUCCESS_STATUSES else "NOT_ASSESSED"),
            validation_status=validation_status or ("VALIDATED_SHAPE" if status in SUCCESS_STATUSES else "NOT_VALIDATED"),
        )

    def dry_run_result(self, dataset: dict[str, Any], context: HydrationContext, started_at: str) -> HydrationResult:
        return self.result(
            dataset,
            context,
            status="PARTIAL",
            started_at=started_at,
            failure_reason="dry_run_no_source_call",
            recommended_user_action="Run with --limit-sample to attempt a small source pull.",
            pit_safe_status="NOT_ASSESSED_DRY_RUN",
            validation_status="DRY_RUN_CLASSIFIED",
        )


def request_get(url: str, *, timeout: int, headers: dict[str, str] | None = None) -> Any:
    import requests

    response = requests.get(url, timeout=timeout, headers=headers or {})
    if response.status_code == 429:
        raise RateLimitedError(f"HTTP 429 rate limited for {url}")
    if response.status_code in {401, 403}:
        raise CredentialOrSubscriptionError(f"HTTP {response.status_code} unauthorized/forbidden for {url}")
    response.raise_for_status()
    return response


class RateLimitedError(RuntimeError):
    pass


class CredentialOrSubscriptionError(RuntimeError):
    pass


def status_from_exception(exc: Exception) -> tuple[str, str]:
    text = str(exc)
    if isinstance(exc, RateLimitedError):
        return "RATE_LIMITED", text
    if isinstance(exc, CredentialOrSubscriptionError):
        return "BLOCKED_AUTH_OR_ENTITLEMENT", text
    if "schema" in text.lower() or "parse" in text.lower() or isinstance(exc, (KeyError, ValueError, TypeError)):
        return "SCHEMA_ERROR", text
    return "SOURCE_UNAVAILABLE", text
