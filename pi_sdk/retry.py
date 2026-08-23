"""Retry helpers for transient LLM API failures (429, 503)."""

from __future__ import annotations

from typing import Any

RETRYABLE_STATUS_CODES = frozenset({429, 503})
DEFAULT_MAX_RETRIES = 3
MAX_BACKOFF_SECONDS = 60.0


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is not None:
        try:
            return int(code)
        except (TypeError, ValueError):
            pass

    response = getattr(exc, "response", None)
    if response is not None:
        response_code = getattr(response, "status_code", None)
        if response_code is not None:
            try:
                return int(response_code)
            except (TypeError, ValueError):
                pass
    return None


def is_rate_limit_error(exc: BaseException) -> bool:
    code = _status_code(exc)
    if code == 429:
        return True
    return type(exc).__name__ == "RateLimitError"


def is_retryable_error(exc: BaseException) -> bool:
    code = _status_code(exc)
    if code in RETRYABLE_STATUS_CODES:
        return True
    if is_rate_limit_error(exc):
        return True
    return False


def _header_value(headers: Any, key: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        val = getter(key) or getter(key.lower()) or getter(key.title())
        if val is not None:
            return str(val).strip() or None
    return None


def retry_after_seconds(exc: BaseException, attempt: int, *, base_delay: float = 1.0) -> float:
    """Seconds to wait before retrying. Honors Retry-After, else exponential backoff."""
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        raw = _header_value(headers, "retry-after") or _header_value(headers, "Retry-After")
        if raw:
            try:
                return min(max(float(raw), 0.0), MAX_BACKOFF_SECONDS)
            except ValueError:
                pass

    delay = base_delay * (2 ** max(0, attempt))
    return min(delay, MAX_BACKOFF_SECONDS)
