"""Pivot API application.

Pivot is a basketball decision-intelligence platform. The product was called RosterLab
through the R1-R7 generation; the release reports under that name are kept as the
provenance record, and a handful of infrastructure identifiers (the `tradelab-backend`
distribution, the default `tradelab.db` filename, the `tradelab:` cache prefix and the TEI
acronym) deliberately keep their historical spelling because renaming them would move a
database, invalidate a cache namespace or change the meaning of a persisted column without
changing anything a user sees. See docs/pivot/adr.md.
"""

import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.v1 import (
    assets,
    assistant,
    comparisons,
    health,
    intelligence,
    players,
    scenarios,
    teams,
    trades,
)
from app.config import get_settings
from app.core.errors import (
    DomainError,
    domain_error_handler,
    http_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(
    title="Pivot: Basketball Intelligence for Better Decisions",
    version=__version__,
    description=(
        "Decision-support API for basketball roster and trade analysis: observe a "
        "roster, diagnose it, compare players, simulate a move, and read why. Basketball "
        "data comes from NBA.com via the `nba_api` package, with full provenance. This "
        "is an analytical portfolio project, not an official NBA cap-management product."
    ),
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# Simple in-process rate limiter (per client IP, sliding minute window). A demo-scale
# guard, not a substitute for infrastructure rate limiting in production.
_request_log: dict[str, deque] = defaultdict(deque)
RATE_LIMIT_PER_MINUTE = 600


@app.middleware("http")
def observability(request: Request, call_next):
    async def run():
        request_id = request.headers.get("x-request-id", str(uuid.uuid4())[:8])
        request.state.request_id = request_id

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        # Asset serving (photos/logos) is cheap, cacheable, and fired dozens of times
        # per page — it is exempt from the API rate window.
        is_asset = request.url.path.startswith(f"{API}/assets/")
        if is_asset:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response
        # `_request_log` is a defaultdict, so reading it for an exempt request still
        # created a permanent key: asset traffic grew the dict the exemption was
        # supposed to keep it out of. The exemption now returns before the read.
        window = _request_log[client_ip]
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= RATE_LIMIT_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests",
                        "request_id": request_id,
                    }
                },
            )
        window.append(now)

        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        return response

    return run()


app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

API = "/api/v1"
# Ask Pivot's Stop button: `observability` above is a BaseHTTPMiddleware, behind which a
# route never sees the client disconnect. This pure-ASGI middleware records it on the scope
# for POST /assistant/ask only, and must be added last so it sits outermost (see its doc).
app.add_middleware(assistant.AskDisconnectMiddleware, path=f"{API}/assistant/ask")
app.include_router(health.router, prefix=API)
app.include_router(teams.router, prefix=API)
app.include_router(players.router, prefix=API)
app.include_router(scenarios.router, prefix=API)
app.include_router(trades.router, prefix=API)
app.include_router(comparisons.router, prefix=API)
app.include_router(intelligence.router, prefix=API)
app.include_router(assets.router, prefix=API)
# A disabled deployment does not advertise the experimental routes (read at import, like
# every other setting here): /status still answers `enabled: false`, but nothing is listed.
app.include_router(assistant.router, prefix=API, include_in_schema=settings.assistant_enabled)


@app.get("/")
def root() -> dict:
    return {
        "name": "Pivot API",
        "version": __version__,
        "docs": "/docs",
        "data_health": f"{API}/data-health",
    }
