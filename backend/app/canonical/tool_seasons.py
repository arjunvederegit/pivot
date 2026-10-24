"""R8.2. The seasons a response is *about*, stated once, in one shape, for every tool.

Before R8.2 one string — ``Settings.current_season`` — governed rosters, statistics and model
estimates. In the summer and preseason that meant "who is on the Lakers" was answered with last
season's roster because last season is where the statistics are. The two have different clocks:

=====================  =========================================  ==============================
Question               Season                                     Turns over
=====================  =========================================  ==============================
Who is on the team?    ``roster_season`` (league year)            1 July
What did they do?      ``performance_season``                     2 weeks into the new season
What do they cost?     ``contract_season`` (``CAP_LEAGUE_YEAR``)  by hand, 1 July
What is forecast?      ``projection_target_season``              1 July (inputs: performance)
=====================  =========================================  ==============================

This module builds the block that says which is which for one response (``tool_seasons``), plus
the small pieces tools need to attribute a statistic honestly (``attribute_stat_team``). It is read
only and reuses the calendar (``SeasonContext``) and the roster module; it owns no data.

The block is **additive**: no existing field of any payload changes meaning (``season`` on a payload
still names the statistics season it always named); the block adds the roster season beside it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.canonical.calendar import SeasonContext, season_context
from app.canonical.rosters import choose_roster_season
from app.canonical.seasons import Season
from app.config import get_settings
from app.db.models import (
    ContractYear,
    ModelVersion,
    PlayerImpactEstimate,
    RosterEntry,
)

PRESEASON_ROSTER_CAVEAT = (
    "NBA.com does not say which list a roster is. In the preseason it can include training-camp "
    "invitees, Exhibit 10 and two-way players, and the 15-player regular-season roster is set after "
    "the cut-down; Pivot cannot tell these apart, so the roster is provisional until then."
)


def season_context_now() -> SeasonContext:
    """Today's calendar position, through ``Settings.season_context`` (the one place the clock is read)."""
    return get_settings().season_context


def tool_seasons(
    db: Session,
    *,
    as_of: date | None = None,
    roster_season: str | None = None,
    performance_season: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The roster / performance / contract / projection seasons for a response, with their state.

    ``roster_season`` and ``performance_season`` override the defaults (an explicit historical
    request). ``as_of`` injects the date for tests; the clock is otherwise read once here.
    """
    settings = get_settings()
    moment = now or datetime.now(UTC)
    ctx = (
        season_context(as_of, grace_days=settings.season_rollover_grace_days)
        if as_of is not None
        else season_context_now()
    )
    notes: list[str] = []

    # ---- roster ------------------------------------------------------------------------
    choice = choose_roster_season(db, roster_season)
    roster = Season.coerce(choice.season)
    count, teams, oldest, newest = db.execute(
        select(
            func.count(RosterEntry.id),
            func.count(distinct(RosterEntry.team_id)),
            func.min(RosterEntry.source_retrieved_at),
            func.max(RosterEntry.source_retrieved_at),
        ).where(RosterEntry.season == roster.label, RosterEntry.is_current)
    ).one()
    is_league_season = roster == ctx.league_season
    if choice.fell_back:
        status = "fallback_previous_snapshot"
    elif is_league_season:
        status = ctx.roster_basis
    else:
        status = "historical_snapshot"
    provisional = status in ("provisional_preseason", "offseason_snapshot", "fallback_previous_snapshot")
    if status == "provisional_preseason":
        notes.append(PRESEASON_ROSTER_CAVEAT)
    elif status == "offseason_snapshot":
        notes.append(
            "offseason roster: free agency, trades and camp invitations are still moving it, so "
            "it is a snapshot, not a final roster"
        )
    if choice.reason:
        notes.append(choice.reason)

    snapshot_age_hours = None
    if newest is not None:
        newest_utc = newest if newest.tzinfo else newest.replace(tzinfo=UTC)
        snapshot_age_hours = round((moment - newest_utc).total_seconds() / 3600, 1)

    # ---- performance ----------------------------------------------------------------------
    perf = Season.coerce(performance_season) if performance_season else Season.coerce(settings.current_season)
    perf_is_completed = perf <= ctx.last_completed_season
    perf_state = (
        "completed_season"
        if perf_is_completed
        else (
            "in_progress"
            if perf == ctx.league_season and ctx.regular_season_stats_expected
            else "not_yet_available"
        )
    )
    behind_roster = perf < roster
    if behind_roster:
        notes.append(
            f"player statistics are {perf.label} regular-season evidence; the roster is "
            f"{roster.label}. A player who changed teams is shown on his {roster.label} team with "
            f"statistics earned for the team named on each statistic."
        )

    # ---- contracts ------------------------------------------------------------------------
    contract_season = settings.cap_league_year
    contract_rows = (
        db.scalar(select(func.count(ContractYear.id)).where(ContractYear.season == contract_season))
        or 0
    )
    contract_state = "available" if contract_rows else "missing"
    if not contract_rows:
        notes.append(
            f"no {contract_season} salary data is on file: salaries are unknown (never zero), and "
            "no earlier season's salary is used in their place"
        )
    elif contract_season != roster.label:
        notes.append(
            f"contracts are keyed to {contract_season}; the roster is {roster.label}"
        )

    # ---- projection / model readiness ------------------------------------------------------
    model = db.scalar(
        select(ModelVersion).where(
            ModelVersion.model_name == "player_impact", ModelVersion.is_active
        )
    )
    estimate_seasons: list[str] = []
    if model is not None:
        estimate_seasons = sorted(
            db.scalars(
                select(PlayerImpactEstimate.season)
                .where(PlayerImpactEstimate.model_version_id == model.id)
                .distinct()
            ).all()
        )
    estimates_for_performance = perf.label in estimate_seasons
    if not estimates_for_performance:
        notes.append(
            f"the active impact model has no estimates for {perf.label}; run `make train && make "
            "score` before relying on impact-based views"
            if model is not None
            else "no impact model is active; run `make train`"
        )

    return {
        "as_of": ctx.as_of.isoformat(),
        "phase": ctx.phase.value,
        "roster": {
            "season": roster.label,
            "preferred_season": choice.preferred,
            "fell_back": choice.fell_back,
            "status": status,
            "provisional": provisional,
            "players": int(count or 0),
            "teams": int(teams or 0),
            "oldest_retrieved_at": oldest.isoformat() if oldest else None,
            "snapshot_retrieved_at": newest.isoformat() if newest else None,
            "snapshot_age_hours": snapshot_age_hours,
            "basis": "observed provider snapshot (NBA.com roster listing), not a transaction record",
        },
        "performance": {
            "season": perf.label,
            "season_type": "regular",
            "label": f"{perf.label} regular season",
            "state": perf_state,
            "is_baseline": perf < ctx.league_season,
            "behind_roster_season": behind_roster,
            "league_season_regular_stats": ctx.regular_stats_state.value,
            "next_rollover_date": ctx.rollover_date.isoformat() if perf < ctx.league_season else None,
        },
        "contract": {
            "season": contract_season,
            "state": contract_state,
            "rows": int(contract_rows),
        },
        "projection": {
            "target_season": ctx.projection_target_season.label,
            "input_season": perf.label,
            "model_active": model is not None,
            "estimate_seasons": estimate_seasons,
            "estimates_for_input_season": estimates_for_performance,
        },
        "pinned": {
            "current_season": settings.pinned_season.strip() or None,
            "roster_season": settings.pinned_roster_season.strip() or None,
        },
        "notes": notes,
    }


def attribute_stat_team(stat_team_id: str | None, roster_team_id: str | None) -> dict[str, Any]:
    """How a statistic's team relates to the player's current team.

    A statistic belongs to the team(s) it was earned with. ``same_team`` is True only when both are
    known and equal; otherwise it is ``None`` (unknown), never ``False`` by default — NBA.com
    publishes a traded player's season line as a combined row without a single team.
    """
    if stat_team_id is None or roster_team_id is None:
        return {"stat_team_id": stat_team_id, "same_team": None}
    return {"stat_team_id": stat_team_id, "same_team": stat_team_id == roster_team_id}


def roster_verification(db: Session, season: str | None = None) -> dict[str, Any]:
    """Is the roster snapshot for ``season`` complete and coherent? Read-only, one report.

    This is what an owner reads after ``make refresh DATASETS=rosters``: coverage, size per team,
    contradictions, how many players moved or arrived since the previous season's snapshot, and how
    many on the roster have no statistics for the performance season (rookies and new signings —
    absent, not zero). It never fixes anything and never claims the list is the final roster.
    """
    from app.db.models import DataSyncRun, Player, PlayerSeasonStats, Team

    settings = get_settings()
    choice = choose_roster_season(db, season)
    label = choice.season
    perf = settings.current_season

    team_count = db.scalar(select(func.count(Team.id))) or 0
    rows = db.execute(
        select(RosterEntry.team_id, RosterEntry.player_id).where(
            RosterEntry.season == label, RosterEntry.is_current
        )
    ).all()
    per_team: dict[str, int] = {}
    teams_of_player: dict[str, set[str]] = {}
    for team_id, player_id in rows:
        per_team[team_id] = per_team.get(team_id, 0) + 1
        teams_of_player.setdefault(player_id, set()).add(team_id)
    sizes = sorted(per_team.values())
    abbreviations = {t.id: t.abbreviation for t in db.scalars(select(Team)).all()}
    missing_teams = sorted(abbreviations[t] for t in abbreviations if t not in per_team)
    unusual = sorted(
        f"{abbreviations.get(t, t)}={n}" for t, n in per_team.items() if n < 13 or n > 21
    )
    conflicts = sorted(p for p, teams in teams_of_player.items() if len(teams) > 1)

    ids = list(teams_of_player)
    rookies = no_history = 0
    if ids:
        have_stats = set(
            db.scalars(
                select(PlayerSeasonStats.player_id).where(
                    PlayerSeasonStats.season == perf,
                    PlayerSeasonStats.season_type == "regular",
                    PlayerSeasonStats.stat_type == "base",
                    PlayerSeasonStats.player_id.in_(ids),
                )
            ).all()
        )
        no_history = len(set(ids) - have_stats)
        rookies = (
            db.scalar(
                select(func.count(Player.id)).where(
                    Player.id.in_(ids), Player.years_experience == 0
                )
            )
            or 0
        )

    previous = Season.coerce(label).previous().label
    prev_team_of: dict[str, set[str]] = {}
    for team_id, player_id in db.execute(
        select(RosterEntry.team_id, RosterEntry.player_id).where(
            RosterEntry.season == previous, RosterEntry.is_current
        )
    ).all():
        prev_team_of.setdefault(player_id, set()).add(team_id)
    changed_team = sum(
        1 for p, teams in teams_of_player.items() if p in prev_team_of and not (teams & prev_team_of[p])
    )
    new_to_roster = sum(1 for p in teams_of_player if p not in prev_team_of)

    last_run = None
    for run in db.scalars(
        select(DataSyncRun)
        .where(DataSyncRun.job_name.in_(("sync_rosters", "prepare_league_rosters")))
        .order_by(DataSyncRun.started_at.desc())
        .limit(40)
    ).all():
        detail = run.detail if isinstance(run.detail, dict) else {}
        if detail.get("season") == label:
            last_run = {
                "job": run.job_name,
                "status": run.status,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "error": f"{run.error_class}: {(run.error_message or '')[:200]}" if run.error_class else None,
                **{k: detail[k] for k in ("added", "confirmed", "closed", "duplicate_rows", "player_on_two_teams", "existing_player_bio_updated") if k in detail},
            }
            break

    issues: list[str] = []
    if choice.fell_back:
        issues.append(f"{choice.reason}; run `make refresh DATASETS=rosters`")
    if not rows:
        issues.append(f"no current roster rows for {label}; run `make refresh DATASETS=rosters`")
    if missing_teams:
        issues.append(f"teams with no roster: {', '.join(missing_teams)}")
    if unusual:
        issues.append(f"unusual roster sizes (outside 13-21): {', '.join(unusual)}")
    if conflicts:
        issues.append(f"{len(conflicts)} player(s) on more than one current roster")
    if last_run and last_run["status"] == "failed":
        issues.append(f"the latest refresh failed: {last_run['error']}")

    return {
        "season": label,
        "players": len(rows),
        "teams_covered": len(per_team),
        "teams_expected": team_count,
        "team_size": {
            "min": sizes[0] if sizes else None,
            "median": sizes[len(sizes) // 2] if sizes else None,
            "max": sizes[-1] if sizes else None,
        },
        "players_on_multiple_current_rosters": len(conflicts),
        "compared_with": previous,
        "players_on_a_different_team_than_previous_season": changed_team,
        "players_new_to_the_roster_listing": new_to_roster,
        "rookies_by_years_experience": int(rookies),
        "players_without_performance_season_statistics": no_history,
        "performance_season": perf,
        "last_refresh": last_run,
        "unresolved": issues,
        "complete": bool(rows) and not missing_teams and not conflicts and not choice.fell_back,
        "provisional_note": PRESEASON_ROSTER_CAVEAT
        if season_context_now().roster_basis == "provisional_preseason" and label == season_context_now().roster_season.label
        else None,
    }
