"""nba_api expressed in the canonical contract (R8.1).

R8 gave Pivot a provider-independent contract — ``ProviderResult`` (did the provider answer, and
was the answer "nothing"?) and typed records (``TeamRecord``, ``GameRecord`` …) — but only the
BALLDONTLIE adapter used it. The ``sync_*`` jobs still spoke nba_api's dict protocol and treated
every exception alike, so "NBA.com has no 2026-27 statistics yet" and "NBA.com is blocking us"
were both just a failed run.

This module is the bridge, kept deliberately thin:

* ``fetch_result`` runs one provider call and returns a ``ProviderResult`` whose *status* says what
  happened — ``OK``, ``EMPTY`` (a well-formed "no rows"), ``UNAVAILABLE``/``RATE_LIMITED``/
  ``SCHEMA_ERROR`` (the answer is unknown). The jobs act on that status.
* the ``*_record`` functions turn the existing normalizers' dicts into canonical records, so the
  same downstream code can consume nba_api and BALLDONTLIE rows.

What it does **not** do is change what is stored. The dict rows the normalizers produce — including
the raw ``stats`` JSON every validated analysis reads — still go to the database untouched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from app.canonical.records import (
    CallMeta,
    GameRecord,
    PlayerRecord,
    PlayerStatRecord,
    ProviderResult,
    ProviderStatus,
    StandingRecord,
    TeamRecord,
    TeamStatRecord,
)
from app.canonical.seasons import Season, SeasonType, StatScope
from app.canonical.stats import Aggregation, numeric

from .exceptions import (
    NBAProviderError,
    ProviderBlockedError,
    ProviderEmptyError,
    ProviderSchemaError,
    ProviderThrottledError,
    ProviderTimeoutError,
)

PROVIDER = "nba_api"


def status_for(exc: BaseException) -> ProviderStatus:
    """Map an nba_api failure onto the canonical status that says what it means."""
    if isinstance(exc, ProviderEmptyError):
        return ProviderStatus.EMPTY
    if isinstance(exc, ProviderSchemaError):
        return ProviderStatus.SCHEMA_ERROR
    if isinstance(exc, ProviderThrottledError):
        return ProviderStatus.RATE_LIMITED
    if isinstance(exc, ProviderBlockedError | ProviderTimeoutError | NBAProviderError):
        return ProviderStatus.UNAVAILABLE
    return ProviderStatus.UNAVAILABLE


def fetch_result(
    endpoint: str,
    call: Callable[[], Coroutine[Any, Any, list[dict[str, Any]]]],
    **params: Any,
) -> tuple[ProviderResult[dict[str, Any]], BaseException | None]:
    """Run ``call`` and describe the outcome canonically.

    Returns ``(result, error)``. ``error`` is the original exception for a non-data outcome (so a
    caller can re-raise it unchanged) and ``None`` for ``OK``/``EMPTY``. An ``EMPTY`` result is an
    *answer*: ``has_answer`` is true and ``records`` is ``()``.
    """
    meta = CallMeta(PROVIDER, endpoint, params={k: str(v) for k, v in params.items()}, requests=1)
    try:
        rows = asyncio.run(call())
    except ProviderEmptyError:
        return ProviderResult.ok([], meta), None
    except Exception as exc:  # noqa: BLE001 — classified, and handed back to the caller
        return ProviderResult.failed(status_for(exc), meta, f"{type(exc).__name__}: {exc}"), exc
    return ProviderResult.ok(rows, meta), None


# --------------------------------------------------------------------------- records


def _numeric_values(stats: dict[str, Any]) -> dict[str, float | None]:
    """The numeric columns of a stat row. Non-numeric ones (names, abbreviations) are dropped;
    an absent or NaN number is ``None``, never 0."""
    out: dict[str, float | None] = {}
    for key, value in stats.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) or value is None:
            out[key] = numeric(value)
    return out


def team_record(row: dict[str, Any]) -> TeamRecord:
    return TeamRecord(
        provider=PROVIDER,
        provider_id=str(row["nba_team_id"]),
        abbreviation=row["abbreviation"],
        full_name=row["full_name"],
        city=row.get("city"),
        nickname=row.get("nickname"),
    )


def roster_player_record(row: dict[str, Any]) -> PlayerRecord:
    """A roster row as a player with a current-team hint."""
    return PlayerRecord(
        provider=PROVIDER,
        provider_id=str(row["nba_player_id"]),
        full_name=row.get("player_name") or f"NBA Player {row['nba_player_id']}",
        position=row.get("position"),
        height_inches=row.get("height_inches"),
        weight_lbs=row.get("weight_lbs"),
        jersey_number=row.get("jersey_number"),
        team_provider_id=str(row["nba_team_id"]),
    )


def standing_record(row: dict[str, Any], season_type: SeasonType = SeasonType.REGULAR) -> StandingRecord:
    details = row.get("details") or {}
    return StandingRecord(
        provider=PROVIDER,
        team_provider_id=str(row["nba_team_id"]),
        scope=StatScope(Season.parse(row["season"]), season_type),
        wins=int(row["wins"]),
        losses=int(row["losses"]),
        conference_rank=row.get("conference_rank"),
        home_record=details.get("HOME"),
        road_record=details.get("ROAD"),
    )


def game_record(row: dict[str, Any], season_type: SeasonType = SeasonType.REGULAR) -> GameRecord | None:
    """A game record, or ``None`` when a side is missing (a log truncated mid-game)."""
    if row.get("home_nba_team_id") is None or row.get("away_nba_team_id") is None:
        return None
    return GameRecord(
        provider=PROVIDER,
        provider_id=str(row["nba_game_id"]),
        season=Season.parse(row["season"]),
        season_type=season_type,
        game_date=row["game_date"],
        home_team_provider_id=str(row["home_nba_team_id"]),
        away_team_provider_id=str(row["away_nba_team_id"]),
        home_score=row.get("home_score"),
        away_score=row.get("away_score"),
        status=row.get("status"),
    )


def player_stat_record(
    row: dict[str, Any], season_type: SeasonType = SeasonType.REGULAR
) -> PlayerStatRecord:
    return PlayerStatRecord(
        provider=PROVIDER,
        player_provider_id=str(row["nba_player_id"]),
        scope=StatScope(Season.parse(row["season"]), season_type),
        stat_group=row["stat_type"],
        aggregation=Aggregation.PER_GAME.value,
        values=_numeric_values(row.get("stats") or {}),
        games_played=row.get("games_played"),
        team_provider_id=str(row["nba_team_id"]) if row.get("nba_team_id") else None,
        basis="estimated" if row["stat_type"] == "estimated" else "observed",
    )


def team_stat_record(
    row: dict[str, Any], season_type: SeasonType = SeasonType.REGULAR
) -> TeamStatRecord:
    return TeamStatRecord(
        provider=PROVIDER,
        team_provider_id=str(row["nba_team_id"]),
        scope=StatScope(Season.parse(row["season"]), season_type),
        stat_group=row["stat_type"],
        aggregation=Aggregation.PER_GAME.value,
        values=_numeric_values(row.get("stats") or {}),
    )


def retrieved_at(row: dict[str, Any]) -> datetime:
    return row.get("source_retrieved_at") or datetime.now(UTC)
