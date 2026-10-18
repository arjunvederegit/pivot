"""HTTP client for api.balldontlie.io: auth, pacing, retries, pagination, classified errors.

Everything that touches the network lives here, so everything that can go wrong with a paid,
rate-limited third party is handled in one place:

* **Credential.** Read from settings (``SecretStr``), sent only as the ``Authorization``
  header, never logged, never placed in an exception message (``_redact``), never returned to
  a browser. With no key configured the client refuses *before* making a request.
* **Pacing.** A minimum interval between request starts derived from the plan's published
  per-minute limit, held 20 % under it. The 48-hour trial is 5 requests/minute (12 s apart);
  GOAT is 600/minute. Both are documented limits, not measured ones.
* **Retries.** 429 honours ``Retry-After`` when sent, else backs off exponentially; 5xx,
  timeouts and connection errors back off and retry; 401, 400 and 404 never retry.
* **A 401 is ambiguous.** The documentation defines 401 as *either* "you need an API key"
  *or* "your account tier does not have access to the endpoint". The status code alone cannot
  say which. The client therefore remembers whether this key has *ever* succeeded: a 401 after
  a success elsewhere is reported as ``TIER_RESTRICTED``, one before any success as
  ``UNAUTHORIZED``. That is an inference, and the message says so.
* **Pagination.** Cursor-based (``meta.next_cursor``), 100 per page at most, with a page cap
  and a repeated-cursor guard so a misbehaving upstream cannot loop the client forever.
  A capped read is returned with a warning, never silently as if it were complete.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.canonical.records import ProviderStatus
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER = "balldontlie"

#: Published per-minute limits by plan (docs.balldontlie.io, "Account Tiers"; retrieved
#: 2026-10-18). The 48-hour trial is capped at 5/minute regardless of the plan it trials.
TIER_LIMITS_PER_MINUTE: dict[str, int] = {
    "free": 5,
    "trial": 5,
    "all_star": 60,
    "goat": 600,
    "all_access": 600,
}
PACING_FACTOR = 0.8
MAX_PER_PAGE = 100


class BalldontlieError(Exception):
    """A classified outcome of one request that produced no payload.

    ``status`` is a failure status, except ``EMPTY`` for a 404 — the provider answered
    that the resource does not exist, which callers treat as data, not as a fault.
    """

    def __init__(
        self,
        status: ProviderStatus,
        endpoint: str,
        message: str,
        http_status: int | None = None,
    ):
        self.status = status
        self.endpoint = endpoint
        self.http_status = http_status
        super().__init__(f"[{endpoint}] {status.value}: {message}")


@dataclass
class PageResult:
    records: list[dict[str, Any]]
    pages: int
    requests: int
    warnings: list[str] = field(default_factory=list)


class RequestPacer:
    """Enforces a minimum interval between request *starts*. Clock and sleep are injected."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.min_interval = 60.0 / (requests_per_minute * PACING_FACTOR)
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._next_start = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next_start - now
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
            self._next_start = max(now, self._next_start) + self.min_interval


class BalldontlieClient:
    def __init__(
        self,
        api_key: str,
        *,
        tier: str = "trial",
        base_url: str = "https://api.balldontlie.io",
        prefix: str = "/nba/v1",
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if tier not in TIER_LIMITS_PER_MINUTE:
            raise ValueError(f"unknown BALLDONTLIE tier {tier!r}; known: {list(TIER_LIMITS_PER_MINUTE)}")
        self._key = api_key.strip()
        self.tier = tier
        self.prefix = "/" + prefix.strip("/")
        self.max_retries = max_retries
        self._sleep = sleep
        self._pacer = RequestPacer(TIER_LIMITS_PER_MINUTE[tier], sleep=sleep, clock=clock)
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self.request_count = 0
        #: Paginated reads that stopped before the end (page cap, repeated cursor). The evaluation
        #: harness turns these into "incomplete" findings instead of letting a partial read pass.
        self.truncations: list[str] = []
        #: True once any request has returned 2xx with this key (see the 401 note above).
        self.authenticated_ok = False

    # ------------------------------------------------------------------ plumbing

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def close(self) -> None:
        self._http.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 120.0)
            except ValueError:
                pass
        return min(2.0 * (2**attempt), 60.0)

    def _url(self, path: str) -> str:
        """``/teams`` -> ``/nba/v1/teams``; ``/v2/stats/advanced`` -> ``/nba/v2/stats/advanced``.

        The OpenAPI document serves a few NBA endpoints (advanced stats, odds) under ``v2``
        beside the ``v1`` ones, so a path that names its own version replaces the prefix's.
        """
        if path.startswith(self.prefix):
            return path
        if path.startswith("/v2/"):
            return self.prefix.rsplit("/", 1)[0] + path
        return f"{self.prefix}{path}"

    # ------------------------------------------------------------------ requests

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One logical GET (retries included). Returns the decoded JSON object."""
        endpoint = path
        if not self._key:
            raise BalldontlieError(
                ProviderStatus.NOT_CONFIGURED, endpoint, "BALLDONTLIE_API_KEY is not set"
            )
        url = self._url(path)
        last: BalldontlieError | None = None
        for attempt in range(self.max_retries + 1):
            self._pacer.wait()
            self.request_count += 1
            try:
                response = self._http.get(
                    url, params=_encode_params(params), headers={"Authorization": self._key}
                )
            except httpx.TimeoutException as exc:
                last = BalldontlieError(
                    ProviderStatus.UNAVAILABLE, endpoint, f"timeout: {self._redact(str(exc))}"
                )
            except httpx.TransportError as exc:
                last = BalldontlieError(
                    ProviderStatus.UNAVAILABLE,
                    endpoint,
                    f"{type(exc).__name__}: {self._redact(str(exc))}",
                )
            else:
                outcome = self._interpret(endpoint, response)
                if isinstance(outcome, dict):
                    self.authenticated_ok = True
                    logger.info(
                        "balldontlie ok",
                        extra={"endpoint": endpoint, "http_status": response.status_code},
                    )
                    return outcome
                last = outcome
                retry_after = response.headers.get("Retry-After")
                if outcome.status not in (ProviderStatus.RATE_LIMITED, ProviderStatus.UNAVAILABLE):
                    break
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt, retry_after))
                    continue
                break
            if attempt < self.max_retries:
                self._sleep(self._backoff(attempt, None))
        assert last is not None
        logger.warning(
            "balldontlie failed",
            extra={"endpoint": endpoint, "status": last.status.value, "http_status": last.http_status},
        )
        raise last

    def _interpret(self, endpoint: str, response: httpx.Response) -> dict[str, Any] | BalldontlieError:
        code = response.status_code
        if code == 401:
            if self.authenticated_ok:
                return BalldontlieError(
                    ProviderStatus.TIER_RESTRICTED,
                    endpoint,
                    "401 after this key succeeded elsewhere: inferred plan restriction "
                    "(the API uses 401 for both a bad key and an endpoint outside the plan)",
                    code,
                )
            return BalldontlieError(
                ProviderStatus.UNAUTHORIZED,
                endpoint,
                "401: missing/invalid key, or the plan excludes this endpoint "
                "(indistinguishable until the key succeeds on a free endpoint)",
                code,
            )
        if code == 429:
            return BalldontlieError(ProviderStatus.RATE_LIMITED, endpoint, "429 rate limited", code)
        if code == 404:
            # The provider answered that the resource does not exist: an answer, not a failure.
            return BalldontlieError(ProviderStatus.EMPTY, endpoint, "404 not found", code)
        if code in (400, 406):
            return BalldontlieError(
                ProviderStatus.SCHEMA_ERROR,
                endpoint,
                f"{code}: the request was rejected as invalid ({_snippet(response)})",
                code,
            )
        if code >= 500:
            return BalldontlieError(ProviderStatus.UNAVAILABLE, endpoint, f"{code} upstream error", code)
        if not 200 <= code < 300:
            return BalldontlieError(ProviderStatus.UNAVAILABLE, endpoint, f"unexpected HTTP {code}", code)
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return BalldontlieError(
                ProviderStatus.SCHEMA_ERROR, endpoint, f"response was not JSON ({_snippet(response)})", code
            )
        if not isinstance(payload, dict):
            return BalldontlieError(
                ProviderStatus.SCHEMA_ERROR, endpoint, "response JSON was not an object", code
            )
        return payload

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = 100,
        per_page: int = MAX_PER_PAGE,
    ) -> PageResult:
        """Follow ``meta.next_cursor`` until exhausted or ``max_pages`` is reached."""
        params = dict(params or {})
        params["per_page"] = min(per_page, MAX_PER_PAGE)
        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_cursors: set[int] = set()
        pages = 0
        requests_before = self.request_count
        while True:
            payload = self.get(path, params)
            data = payload.get("data")
            if not isinstance(data, list):
                raise BalldontlieError(
                    ProviderStatus.SCHEMA_ERROR, path, "paginated response has no 'data' array"
                )
            records.extend(r for r in data if isinstance(r, dict))
            pages += 1
            meta = payload.get("meta") or {}
            cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            if cursor is None:
                break
            if cursor in seen_cursors:
                warnings.append(f"cursor {cursor} repeated; stopped to avoid a loop")
                break
            seen_cursors.add(cursor)
            if pages >= max_pages:
                warnings.append(
                    f"stopped at max_pages={max_pages} with more data available: result is TRUNCATED"
                )
                break
            params["cursor"] = cursor
        self.truncations.extend(f"{path}: {w}" for w in warnings)
        return PageResult(records, pages, self.request_count - requests_before, warnings)


def _encode_params(params: dict[str, Any] | None) -> list[tuple[str, str | int | float | bool | None]]:
    """Lists repeat the key as written; scalars pass through; ``None`` is dropped.

    Callers spell array parameters exactly as the OpenAPI document does — ``seasons[]`` on
    ``/games`` but ``player_ids`` on ``/season_averages/{type}`` — because the document is
    not consistent and the client must not guess.
    """
    out: list[tuple[str, str | int | float | bool | None]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            out.extend((key, str(v)) for v in value)
        elif isinstance(value, bool):
            out.append((key, "true" if value else "false"))
        else:
            out.append((key, str(value)))
    return out


_TAG = re.compile(r"<[^>]+>")


def _snippet(response: httpx.Response) -> str:
    text = _TAG.sub(" ", response.text or "")
    return " ".join(text.split())[:160]
