"""Roster membership: one definition of "on a team right now", and the dating that goes with it.

Before R8 the clause ``RosterEntry.season == <current season> AND RosterEntry.is_current`` was
hand-written in a dozen places — the team page, the player directory, the trade evaluator, the
payroll summary, the CBA resolver, the needs scorer — and once *without the season*
(``analytics/train.py`` built its "rostered players" set from every ``is_current`` row in the
table, which is only correct while exactly one season has ever been synced). Eleven copies of
a definition is eleven places for it to drift.

This module is that definition, in two forms:

* ``current_roster_clause`` — the SQL predicate, for callers with their own tuned query (the
  trade evaluator's query budget is a tested property and must not move).
* ``current_roster`` / ``current_team_of`` — the retrieval API for callers that also want to
  know **how old the membership is** and whether it is **contradictory**.

Membership here is an *observed snapshot*, not a league transaction record. ``valid_from`` is
when Pivot first saw the player on that team, and ``source_retrieved_at`` is the last time the
provider confirmed it; neither is the date a trade was made. Payloads say so
(``basis: observed``), and a snapshot older than its freshness window reports ``stale``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session, joinedload

from app.canonical.provenance import (
    SourceMeta,
    build_source_meta,
    source_meta_from_aggregate,
)
from app.canonical.seasons import Season
from app.config import get_settings
from app.db.models import RosterEntry, Team


def resolve_season(season: str | Season | None = None) -> str:
    """The season label to use: the argument if given, else the configured current season.

    Always validated, so a typo'd ``CURRENT_SEASON`` fails here rather than silently
    matching no rows everywhere.
    """
    return Season.coerce(season if season is not None else get_settings().current_season).label


def current_roster_clause(season: str | Season | None = None) -> ColumnElement[bool]:
    """``RosterEntry`` rows that are the latest snapshot for ``season``."""
    return and_(RosterEntry.season == resolve_season(season), RosterEntry.is_current)


@dataclass
class RosterSnapshot:
    season: str
    team_id: str | None
    entries: list[RosterEntry]
    meta: SourceMeta
    #: ``{player_id: [team_id, ...]}`` for any player on more than one current roster in
    #: this snapshot. Empty when membership is consistent.
    conflicts: dict[str, list[str]] = field(default_factory=dict)

    @property
    def player_ids(self) -> list[str]:
        return [e.player_id for e in self.entries]


def current_roster(
    db: Session,
    team_id: str | None = None,
    season: str | Season | None = None,
    *,
    order_by: Any = None,
    now: datetime | None = None,
) -> RosterSnapshot:
    """The current-season roster for one team (or the whole league), with its provenance."""
    label = resolve_season(season)
    stmt = (
        select(RosterEntry)
        .options(joinedload(RosterEntry.player))
        .where(current_roster_clause(label))
    )
    if team_id is not None:
        stmt = stmt.where(RosterEntry.team_id == team_id)
    stmt = stmt.order_by(order_by if order_by is not None else RosterEntry.player_id)
    entries = list(db.scalars(stmt).unique().all())

    expected = None
    notes: list[str] = []
    if team_id is None:
        expected = db.scalar(select(func.count(Team.id))) or None
        covered = len({e.team_id for e in entries})
        if expected and covered < expected:
            notes.append(f"{covered} of {expected} teams have a current roster snapshot")
    meta = build_source_meta(
        "rosters",
        entries,
        season=label,
        season_type=None,
        # League scope: completeness is teams covered / teams known. Team scope has no
        # external expectation to compare a roster's size against, so none is claimed.
        expected_count=None,
        notes=notes,
        now=now,
    )
    if team_id is None and expected:
        meta.expected_count = expected
        meta.completeness = round(min(len({e.team_id for e in entries}) / expected, 1.0), 4)
    meta.notes.append(
        "membership is an observed snapshot: effective_from is when Pivot first saw the "
        "player on this team, not the date of the transaction"
    )

    by_player: dict[str, list[str]] = {}
    for entry in entries:
        by_player.setdefault(entry.player_id, []).append(entry.team_id)
    conflicts = {pid: teams for pid, teams in by_player.items() if len(set(teams)) > 1}
    if conflicts:
        meta.notes.append(
            f"{len(conflicts)} player(s) appear on more than one current roster; "
            "membership is contradictory for them"
        )
    return RosterSnapshot(label, team_id, entries, meta, conflicts)


@dataclass(frozen=True)
class TeamMembership:
    """Where a player is, stated so that "not found" cannot be mistaken for "free agent"."""

    status: str  # member | unlisted | conflict
    team: Team | None
    season: str
    meta: SourceMeta


def current_team_of(
    db: Session,
    player_id: str,
    season: str | Season | None = None,
    *,
    explain_absence: bool = True,
) -> TeamMembership:
    """The player's current-season team.

    ``unlisted`` means no current roster snapshot contains the player. It is *not* a claim
    that the player is a free agent or retired — the snapshot may be stale or incomplete,
    and the attached ``meta`` says how stale.
    """
    label = resolve_season(season)
    entries = list(
        db.scalars(
            select(RosterEntry)
            .options(joinedload(RosterEntry.team))
            .where(current_roster_clause(label), RosterEntry.player_id == player_id)
        )
        .unique()
        .all()
    )
    if not entries and not explain_absence:
        # Bulk callers (a player directory) want the answer, not a per-row freshness query.
        return TeamMembership("unlisted", None, label, build_source_meta("rosters", [], season=label))
    if not entries:
        # Report the age of the *league* snapshot the absence was observed in: "not on any
        # roster" means something different against a 6-hour-old snapshot than a 3-month-old one.
        oldest, newest, count = db.execute(
            select(
                func.min(RosterEntry.source_retrieved_at),
                func.max(RosterEntry.source_retrieved_at),
                func.count(RosterEntry.id),
            ).where(current_roster_clause(label))
        ).one()
        meta = source_meta_from_aggregate(
            "rosters",
            count=count,
            oldest=oldest,
            newest=newest,
            season=label,
            notes=[
                "player is on no current roster in this snapshot; that does not establish "
                "free agency or retirement"
            ],
        )
        return TeamMembership("unlisted", None, label, meta)
    meta = build_source_meta(
        "rosters",
        entries,
        season=label,
        notes=["membership is an observed snapshot, not a transaction record"],
    )
    if len({e.team_id for e in entries}) > 1:
        return TeamMembership("conflict", None, label, meta)
    return TeamMembership("member", entries[0].team, label, meta)
