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
from datetime import UTC, datetime
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


@dataclass(frozen=True)
class RosterSeasonChoice:
    """Which season's roster the tools read, and whether that is the one they wanted."""

    season: str
    #: The season rosters *should* come from (``Settings.roster_season_preferred``).
    preferred: str
    #: True when no current snapshot exists for ``preferred`` yet and an earlier one is served
    #: instead. The caller must say so: it is last season's roster, not this one's.
    fell_back: bool
    reason: str | None = None


def choose_roster_season(db: Session, season: str | Season | None = None) -> RosterSeasonChoice:
    """R8.2. The roster season to read: independent of the statistics season.

    An explicit ``season`` is honoured as given (a historical session asks for what it asks for).
    Otherwise the preferred roster season is used — the league year, not the analysis season — so a
    roster is **not** held back to last season's because this season's statistics do not exist.
    The one thing that can hold it back is the roster itself: when no current snapshot has been
    ingested for the preferred season, the most recent earlier snapshot is served and the choice
    says so (``fell_back``) rather than presenting an empty roster or a stale one unlabelled.
    """
    if season is not None:
        label = Season.coerce(season).label
        return RosterSeasonChoice(label, label, False)
    preferred = Season.coerce(get_settings().roster_season_preferred).label
    best = db.scalar(
        select(RosterEntry.season)
        .where(RosterEntry.is_current, RosterEntry.season <= preferred)
        .order_by(RosterEntry.season.desc())
        .limit(1)
    )
    if best is None or best == preferred:
        return RosterSeasonChoice(preferred, preferred, False)
    return RosterSeasonChoice(
        best,
        preferred,
        True,
        f"no current {preferred} roster snapshot has been ingested; showing the {best} snapshot",
    )


def resolve_roster_season(db: Session, season: str | Season | None = None) -> str:
    """The roster season label (see ``choose_roster_season``)."""
    return choose_roster_season(db, season).season


def current_roster_clause(season: str | Season | None = None) -> ColumnElement[bool]:
    """``RosterEntry`` rows that are the latest snapshot for ``season``.

    R8.2: with no ``season`` this is the *preferred roster season*, not the statistics season.
    Callers with a session should pass ``resolve_roster_season(db)`` so a missing snapshot falls
    back visibly rather than matching no rows.
    """
    label = (
        Season.coerce(season).label
        if season is not None
        else Season.coerce(get_settings().roster_season_preferred).label
    )
    return and_(RosterEntry.season == label, RosterEntry.is_current)


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
    """The current roster for one team (or the whole league), with its provenance.

    R8.2: ``season`` defaults to the *roster* season (``resolve_roster_season``), not the
    statistics season.
    """
    choice = choose_roster_season(db, season)
    label = choice.season
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
    if choice.reason:
        meta.notes.append(choice.reason)
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
    label = resolve_roster_season(db, season)
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


def roster_as_of(
    db: Session,
    as_of: datetime,
    team_id: str | None = None,
    season: str | Season | None = None,
) -> RosterSnapshot:
    """Who was on the roster at ``as_of``, reconstructed from Pivot's own sightings.

    A roster row is open from ``valid_from`` (first seen on that team) to ``valid_to`` (the refresh
    that no longer listed him; null while he is still listed). Rows therefore answer "was he
    there?" only from Pivot's first snapshot onward. Before that the honest answer is *unknown*,
    not *empty*, and the returned ``meta`` says so rather than presenting zero players as a roster.
    """
    label = resolve_roster_season(db, season)
    moment = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    stmt = (
        select(RosterEntry)
        .options(joinedload(RosterEntry.player))
        .where(RosterEntry.season == label)
    )
    if team_id is not None:
        stmt = stmt.where(RosterEntry.team_id == team_id)
    candidates = list(db.scalars(stmt).unique().all())
    entries = [
        e
        for e in candidates
        if (valid_from := _utc(e.valid_from)) is not None
        and valid_from <= moment
        and ((valid_to := _utc(e.valid_to)) is None or valid_to > moment)
    ]
    entries.sort(key=lambda e: e.player_id)
    first_seen = min((v for e in candidates if (v := _utc(e.valid_from)) is not None), default=None)
    notes = [
        f"reconstructed as of {moment.date().isoformat()} from first-seen / last-listed times, "
        "not from league transaction records"
    ]
    if first_seen is None or moment < first_seen:
        notes.append(
            "Pivot has no roster snapshot this early"
            + (f" (its first is {first_seen.date().isoformat()})" if first_seen else "")
            + "; membership at this date is unknown, not empty"
        )
    meta = build_source_meta("rosters", entries, season=label, notes=notes, now=moment)
    return RosterSnapshot(label, team_id, entries, meta)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
