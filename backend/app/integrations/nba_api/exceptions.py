"""Classified provider errors. Every failure from NBA.com is mapped onto one of these
so callers can distinguish transient throttling from schema drift or hard blocks."""


class NBAProviderError(Exception):
    """Base class for all nba_api integration failures."""

    classification = "unknown"

    def __init__(self, endpoint: str, message: str):
        self.endpoint = endpoint
        self.message = message
        super().__init__(f"[{endpoint}] {message}")


class ProviderTimeoutError(NBAProviderError):
    classification = "timeout"


class ProviderThrottledError(NBAProviderError):
    """HTTP 429 or connection resets consistent with rate limiting."""

    classification = "throttled"


class ProviderBlockedError(NBAProviderError):
    """Akamai/edge denial (403/Access Denied HTML instead of JSON)."""

    classification = "blocked"


class ProviderSchemaError(NBAProviderError):
    """Response arrived but required datasets/columns are missing — schema drift."""

    classification = "schema_mismatch"


class ProviderEmptyError(ProviderSchemaError):
    """The provider answered correctly and the answer is "no rows".

    A subclass of ``ProviderSchemaError`` so existing ``except`` clauses still catch it, but a
    different *fact*: the response was well-formed. It is raised after the retry loop, so it is
    never retried, never counts toward the circuit breaker and never marks the endpoint unhealthy.
    Whether "no rows" is expected (2026-27 statistics before opening night) or a defect (2025-26
    statistics) is the caller's judgement, made against the season calendar.
    """

    classification = "empty"


class ProviderUnavailableError(NBAProviderError):
    """Circuit breaker open or upstream hard down."""

    classification = "unavailable"


def classify_exception(endpoint: str, exc: Exception) -> NBAProviderError:
    """Map arbitrary transport exceptions onto a classified provider error."""
    if isinstance(exc, NBAProviderError):
        return exc
    name = type(exc).__name__
    text = str(exc)[:300]
    lowered = (name + " " + text).lower()
    if "timeout" in lowered or "timed out" in lowered:
        return ProviderTimeoutError(endpoint, text)
    if "429" in lowered or "too many requests" in lowered:
        return ProviderThrottledError(endpoint, text)
    if "403" in lowered or "access denied" in lowered or "jsondecode" in lowered:
        # NBA.com edge blocks return HTML "Access Denied" pages, which surface as
        # JSONDecodeError inside nba_api.
        return ProviderBlockedError(endpoint, text)
    if "connection" in lowered or "reset" in lowered or "remote end closed" in lowered:
        return ProviderThrottledError(endpoint, text)
    return ProviderUnavailableError(endpoint, f"{name}: {text}")
