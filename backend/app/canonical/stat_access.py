"""Reading statistics back out: scope-explicit, unit-explicit, missing-explicit.

Every consumer that wants "this player's 2025-26 numbers" used to write its own
``select(PlayerSeasonStats).where(season == ..., stat_type == ...)`` — with no season type, no
statement of what the numbers are per, and a missing player silently absent from a dict (or, in
the worst case, defaulted to 0 by the caller). ``player_stat_rows`` is the shared form:

* the **scope** (season + season type) is a required, validated argument — a playoff row can
  never be returned for a regular-season question, because it filters on it;
* the **definition** of the figures (per-game vs total, observed vs estimated) travels with the
  result as ``spec``;
* the players for whom **no row exists** are returned as ``missing_player_ids`` — an explicit
  list, not a zero and not a silent omission;
* a ``SourceMeta`` reports how fresh and how complete the set is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.provenance import SourceMeta, build_source_meta
from app.canonical.rosters import resolve_season
from app.canonical.seasons import Season, SeasonType, StatScope
from app.canonical.stats import StatTypeSpec, numeric, spec_for
from app.db.models import PlayerSeasonStats, TeamSeasonStats


@dataclass
class StatRows:
    scope: StatScope
    spec: StatTypeSpec
    rows: dict[str, PlayerSeasonStats]
    requested_player_ids: list[str]
    meta: SourceMeta
    missing_player_ids: list[str] = field(default_factory=list)

    def value(self, player_id: str, key: str) -> float | None:
        """One figure for one player; ``None`` when the player or the figure is absent."""
        row = self.rows.get(player_id)
        if row is None:
            return None
        return numeric((row.stats or {}).get(key))


def player_stat_rows(
    db: Session,
    player_ids: list[str],
    *,
    season: str | Season | None = None,
    stat_type: str = "base",
    season_type: SeasonType = SeasonType.REGULAR,
    now: datetime | None = None,
) -> StatRows:
    label = resolve_season(season)
    spec = spec_for(stat_type)  # raises for a stat_type with no definition
    scope = StatScope(Season.parse(label), season_type)
    requested = list(dict.fromkeys(player_ids))
    rows: dict[str, PlayerSeasonStats] = {}
    if requested:
        for row in db.scalars(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id.in_(requested),
                PlayerSeasonStats.season == label,
                PlayerSeasonStats.season_type == season_type.value,
                PlayerSeasonStats.stat_type == stat_type,
            )
        ).all():
            rows[row.player_id] = row
    missing = [pid for pid in requested if pid not in rows]
    notes = [spec.description]
    if missing:
        notes.append(
            f"{len(missing)} of {len(requested)} requested players have no {stat_type} row "
            f"for {scope}; they are absent, not zero"
        )
    meta = build_source_meta(
        "player_stats",
        rows.values(),
        season=label,
        season_type=season_type.value,
        expected_count=len(requested) or None,
        basis=spec.basis,
        notes=notes,
        now=now,
    )
    return StatRows(scope, spec, rows, requested, meta, missing)


def team_stat_rows(
    db: Session,
    team_ids: list[str],
    *,
    season: str | Season | None = None,
    stat_type: str = "base",
    season_type: SeasonType = SeasonType.REGULAR,
    now: datetime | None = None,
) -> tuple[dict[str, TeamSeasonStats], SourceMeta]:
    label = resolve_season(season)
    requested = list(dict.fromkeys(team_ids))
    rows = {
        r.team_id: r
        for r in db.scalars(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id.in_(requested),
                TeamSeasonStats.season == label,
                TeamSeasonStats.season_type == season_type.value,
                TeamSeasonStats.stat_type == stat_type,
            )
        ).all()
    }
    meta = build_source_meta(
        "team_stats",
        rows.values(),
        season=label,
        season_type=season_type.value,
        expected_count=len(requested) or None,
        now=now,
    )
    return rows, meta
