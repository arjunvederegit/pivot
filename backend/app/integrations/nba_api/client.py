"""Single execution path for every nba_api call.

All NBA.com traffic flows through NBAApiClient.fetch_dataframe: rate limiting,
bounded retries with backoff, circuit breaking, schema validation, response caching,
and health metrics live here — never in routers or business logic. nba_api itself is
synchronous; async callers wrap these methods with anyio.to_thread."""

import time
from collections.abc import Callable
from typing import Any

import pandas as pd

from app.config import get_settings
from app.core.cache import get_cache
from app.core.logging import get_logger

from .exceptions import classify_exception
from .headers import build_headers, build_proxy
from .rate_limiter import get_rate_limiter
from .retry import with_retries
from .schemas import validate_dataframe

logger = get_logger(__name__)


class NBAApiClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.cache = get_cache()

    def fetch_dataframe(
        self,
        endpoint_name: str,
        build: Callable[[dict[str, Any]], Any],
        dataset_index: int = 0,
        cache_key: str | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Execute an nba_api endpoint and return (validated dataframe, provenance meta).

        `build` receives common kwargs (headers/timeout/proxy) and must return an
        instantiated nba_api endpoint object.
        """
        if cache_key:
            cached = self.cache.get_json("nba:" + cache_key)
            if cached is not None:
                df = pd.DataFrame(cached["records"])
                return df, {**cached["meta"], "from_cache": True}

        common: dict[str, Any] = {"timeout": int(self.settings.nba_api_timeout_seconds)}
        headers = build_headers()
        if headers:
            common["headers"] = headers
        proxy = build_proxy()
        if proxy:
            common["proxy"] = proxy

        def run() -> pd.DataFrame:
            with get_rate_limiter():
                started = time.monotonic()
                endpoint_obj = build(common)
                frames = endpoint_obj.get_data_frames()
                latency_ms = (time.monotonic() - started) * 1000
            from .health import get_provider_health

            get_provider_health().record_success(endpoint_name, latency_ms)
            if dataset_index >= len(frames):
                from .exceptions import ProviderSchemaError

                raise ProviderSchemaError(
                    endpoint_name, f"dataset index {dataset_index} missing ({len(frames)} datasets)"
                )
            return validate_dataframe(endpoint_name, frames[dataset_index], require_rows=False)

        try:
            df = with_retries(endpoint_name, run, self.settings.nba_api_max_retries)
        except Exception as exc:
            error = classify_exception(endpoint_name, exc)
            from .health import get_provider_health

            get_provider_health().record_failure(endpoint_name, str(error))
            raise error from exc

        if df.empty:
            # Well-formed and empty: reported as such, after the retry loop and the breaker, and
            # deliberately not cached so the next call asks again.
            from .exceptions import ProviderEmptyError

            raise ProviderEmptyError(endpoint_name, "response contained zero rows")
        meta = {
            "provider": "nba_api",
            "upstream": "NBA.com",
            "endpoint": endpoint_name,
            "retrieved_at": time.time(),
            "rows": len(df),
            "from_cache": False,
        }
        if cache_key:
            self.cache.set_json(
                "nba:" + cache_key,
                {"records": df.to_dict(orient="records"), "meta": meta},
                self.settings.nba_api_cache_ttl_seconds,
            )
        return df, meta


_client: NBAApiClient | None = None


def get_client() -> NBAApiClient:
    global _client
    if _client is None:
        _client = NBAApiClient()
    return _client
