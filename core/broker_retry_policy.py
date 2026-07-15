"""Classification policy for retryable read-only broker failures."""

from __future__ import annotations

import re


RETRYABLE_HTTP_STATUSES = {408, 425, 429}
_RETRYABLE_TEXT_MARKERS = (
    "request timed out",
    "timed out",
    "timeout",
    "too many requests",
    "rate limit",
    "connection reset",
    "connection aborted",
    "connection refused",
    "connection error",
    "network is unreachable",
    "name resolution",
    "name or service not known",
    "temporary failure in name resolution",
    "remote end closed connection",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)


def is_retryable_broker_read_error(error: BaseException | str) -> bool:
    """Return true only for transient timeout/connection/rate-limit/5xx reads."""

    explicit_statuses: list[int] = []
    transient_exception = False
    current: BaseException | None = error if isinstance(error, BaseException) else None
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (TimeoutError, ConnectionError)):
            transient_exception = True
        response = getattr(current, "response", None)
        raw_status = getattr(current, "status_code", None) or getattr(response, "status_code", None)
        try:
            explicit_statuses.append(int(raw_status))
        except (TypeError, ValueError):
            pass
        current = current.__cause__ or current.__context__

    message = str(error or "").strip().lower()
    explicit_statuses.extend(
        int(match)
        for match in re.findall(
            r"(?:http\s+|status(?:_code)?\s*[=:]\s*|code[\"']?\s*:\s*)(\d{3})(?!\d)",
            message,
        )
    )
    if any(status in RETRYABLE_HTTP_STATUSES or 500 <= status <= 599 for status in explicit_statuses):
        return True
    if any(400 <= status <= 499 for status in explicit_statuses):
        return False
    if transient_exception:
        return True
    if any(marker in message for marker in _RETRYABLE_TEXT_MARKERS):
        return True
    return False
