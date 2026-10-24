"""Scoring pipeline: team needs from current stats + roster composition.

Run after ingestion and training (`make score`). Needs are transparent percentile
rules (see needs.py) — results are persisted with explanations for the UI."""

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.canonical.rosters import current_roster_clause
from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    Player,
    PlayerSeasonStats,
    RosterEntry,
    Team,
    TeamNeed,
    TeamSeasonStats,
)

from .needs import compute_team_needs

logger = get_logger(__name__)


def _league_stats_frame(db: Session, season: str) -> pd.DataFrame:
    """One row per team; columns prefixed base_/advanced_."""
    rows: dict[str, dict[str, Any]] = {}
    for stats in db.scalars(select(TeamSeasonStats).where(TeamSeasonStats.season == season)).all():
        entry = rows.setdefault(stats.team_id, {"team_id": stats.team_id})
        for key, value in (stats.stats or {}).items():
            entry[f"{stats.stat_type}_{key}"] = value
    return pd.DataFrame(list(rows.values()))


def _advanced_ast_pct(db: Session, season: str) -> dict[str, float]:
    """`{player_id: AST_PCT}` for the season, in one query.

    `_roster_profile` ran this lookup once per rostered player — up to 530 extra
    SELECTs per `make score`, on top of the lazy `RosterEntry.player` load beside it.
    Cached on the session so all 30 teams share one pass.
    """
    # Historical key spelling, kept on purpose: it namespaces a live per-session cache.
    cache = db.info.setdefault("rosterlab_ast_pct", {})
    if season not in cache:
        by_player: dict[str, float] = {}
        for row in db.scalars(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.season == season,
                PlayerSeasonStats.season_type == "regular",
                PlayerSeasonStats.stat_type == "advanced",
            )
        ).all():
            value = (row.stats or {}).get("AST_PCT")
            if value is not None:
                by_player[row.player_id] = float(value)
        cache[season] = by_player
    return cache[season]


def _roster_profile(db: Session, team_id: str, season: str) -> dict[str, Any] | None:
    entries = db.scalars(
        # `joinedload` rather than the relationship's lazy default: `e.player` below
        # otherwise emits one SELECT per rostered player.
        select(RosterEntry)
        .options(joinedload(RosterEntry.player))
        .where(RosterEntry.team_id == team_id, current_roster_clause(season))
    ).all()
    if not entries:
        return None
    heights = [e.player.height_inches for e in entries if e.player.height_inches]
    ages = [e.age for e in entries if e.age]

    # High-assist creators: AST_PCT >= 25% in current-season advanced stats
    ast_pct_by_player = _advanced_ast_pct(db, season)
    creator_count = sum(
        1 for e in entries if ast_pct_by_player.get(e.player_id, 0.0) >= 0.25
    )
    return {
        "avg_height": sum(heights) / len(heights) if heights else None,
        "avg_age": sum(ages) / len(ages) if ages else None,
        "n_creators": creator_count,
        "roster_size": len(entries),
    }


def score_all(db: Session) -> dict[str, Any]:
    settings = get_settings()
    season = settings.current_season
    league = _league_stats_frame(db, season)
    if league.empty:
        return {"error": "no team stats ingested; run `make sync-data` first"}

    teams = db.scalars(select(Team)).all()
    total_needs = 0
    for team in teams:
        team_stats: dict[str, dict] = {}
        for stats in db.scalars(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team.id, TeamSeasonStats.season == season
            )
        ).all():
            team_stats[stats.stat_type] = stats.stats or {}
        if not team_stats:
            continue
        profile = _roster_profile(db, team.id, season)
        needs = compute_team_needs(team_stats, league, profile, team_id=team.id)

        # Update in place, insert what is new, delete what no longer applies.
        #
        # This was a delete-then-add of the same rows. With `autoflush=False`, both sat
        # in one flush, and SQLAlchemy emits a mapper's INSERTs before its DELETEs — so
        # `make score` raised
        #     UNIQUE constraint failed: team_needs.team_id, team_needs.season, need_key
        # on **any database that had already been scored**. It only ever worked the
        # first time, which is why a fresh clone never saw it.
        existing = {
            row.need_key: row
            for row in db.scalars(
                select(TeamNeed).where(TeamNeed.team_id == team.id, TeamNeed.season == season)
            ).all()
        }
        for need in needs:
            row = existing.pop(need.need_key, None)
            if row is None:
                db.add(
                    TeamNeed(
                        team_id=team.id,
                        season=season,
                        need_key=need.need_key,
                        severity=need.severity,
                        percentile=need.percentile,
                        explanation=need.explanation,
                    )
                )
            else:
                row.severity = need.severity
                row.percentile = need.percentile
                row.explanation = need.explanation
            total_needs += 1
        # A rule that no longer produces a need for this team must not leave its old row
        # behind claiming otherwise.
        for stale in existing.values():
            db.delete(stale)
    db.commit()
    logger.info("scored team needs: %d rows", total_needs)
    return {"teams": len(teams), "needs_written": total_needs, "season": season}


def player_count_check(db: Session) -> int:
    return len(db.scalars(select(Player).where(Player.is_active)).all())
