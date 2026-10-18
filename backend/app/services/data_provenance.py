"""Source transparency for the whole database, in one place.

``/data-health`` reports table-level counts and a single staleness flag based on each table's
*newest* row. This reports the question a decision-maker asks instead: **for the datasets the
intelligence tools actually read, how old is the oldest row, how complete is the set against
what it should cover, and who supplied it?** — as the same ``SourceMeta`` every data-bearing
endpoint attaches, so the two always agree.

It also lists the provider registry: which providers Pivot can read, whether each is
configured, and (never) any credential.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.canonical.calendar import SeasonContext, season_context, today_in_league
from app.canonical.provenance import Freshness, SourceMeta, source_meta_from_aggregate
from app.canonical.rosters import current_roster_clause, resolve_season
from app.canonical.seasons import Season, SeasonType
from app.canonical.stats import spec_for
from app.config import get_settings
from app.db.models import (
    Contract,
    ContractYear,
    DataSyncRun,
    Game,
    Injury,
    LeagueCapParameters,
    MediaAsset,
    PlayerImpactEstimate,
    PlayerSeasonStats,
    ProviderEntityId,
    RosterEntry,
    Standing,
    Team,
    TeamSeasonStats,
)
from app.integrations.contracts import get_contract_provider


def _aggregate(db: Session, model: Any, *where: Any) -> tuple[int, Any, Any, list[str]]:
    count, oldest, newest = db.execute(
        select(
            func.count(model.id),
            func.min(model.source_retrieved_at),
            func.max(model.source_retrieved_at),
        ).where(*where)
    ).one()
    providers = [
        p for (p,) in db.execute(select(model.source_provider).where(*where).distinct()) if p
    ]
    return count, oldest, newest, providers


#: The ingestion job whose runs describe each dataset's refresh health.
REFRESH_JOB = {
    "rosters": "sync_rosters",
    "standings": "sync_standings",
    "player_stats": "sync_player_stats",
    "team_stats": "sync_team_stats",
    "games": "sync_games",
    "contracts": "sync_contracts",
}


def _last_refresh(db: Session, job: str) -> dict[str, Any] | None:
    """The newest attempt at ``job`` — any outcome — and the newest success."""
    last = db.scalar(
        select(DataSyncRun).where(DataSyncRun.job_name == job).order_by(DataSyncRun.started_at.desc())
    )
    if last is None:
        return None
    success = db.scalar(
        select(func.max(DataSyncRun.finished_at)).where(
            DataSyncRun.job_name == job, DataSyncRun.status == "succeeded"
        )
    )
    detail = last.detail if isinstance(last.detail, dict) else {}
    return {
        "job": job,
        "status": last.status,
        "outcome": detail.get("outcome"),
        "started_at": last.started_at.isoformat() if last.started_at else None,
        "finished_at": last.finished_at.isoformat() if last.finished_at else None,
        "rows_written": last.rows_written,
        "error": f"{last.error_class}: {(last.error_message or '')[:300]}" if last.error_class else None,
        "last_success_at": success.isoformat() if success else None,
    }


def _attach_refresh(
    db: Session, metas: dict[str, SourceMeta], *, rosters_job: str = "sync_rosters"
) -> None:
    for name, meta in metas.items():
        job = rosters_job if name == "rosters" else REFRESH_JOB.get(name)
        if job is None:
            continue
        info = _last_refresh(db, job)
        meta.last_refresh = info
        if info and info["status"] == "failed":
            meta.refresh_failing = True
            meta.notes.append(
                f"the latest {job} run failed ({info['error']}); the rows above are from the last "
                "successful refresh and have been left untouched"
            )


def dataset_freshness(
    db: Session, season: str | Season | None = None, *, now: datetime | None = None
) -> dict[str, SourceMeta]:
    """Every dataset the tools read, for ``season`` (default: the analysis season).

    Each entry is judged against the calendar, not just a clock: see
    ``app.canonical.provenance.assess_lifecycle``.
    """
    settings = get_settings()
    season = resolve_season(season)
    now = now or datetime.now(UTC)
    ctx = season_context(today_in_league(now), grace_days=settings.season_rollover_grace_days)
    regular = SeasonType.REGULAR.value
    team_count = db.scalar(select(func.count(Team.id))) or 0
    common: dict[str, Any] = {"now": now, "context": ctx}

    metas: dict[str, SourceMeta] = {}

    count, oldest, newest, providers = _aggregate(db, RosterEntry, current_roster_clause(season))
    covered_teams = (
        db.scalar(
            select(func.count(distinct(RosterEntry.team_id))).where(current_roster_clause(season))
        )
        or 0
    )
    notes = ["observed snapshot: effective dates are first-seen times, not transaction dates"]
    meta = source_meta_from_aggregate(
        "rosters",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=season,
        expected_count=team_count or None,
        notes=notes,
        **common,
    )
    # Rosters are measured against teams covered, not rows: 30 teams is the expectation.
    if team_count:
        meta.present_count = covered_teams
        meta.completeness = round(min(covered_teams / team_count, 1.0), 4)
    metas["rosters"] = meta

    last_game = db.scalar(
        select(func.max(Game.game_date)).where(Game.season == season)
    )

    count, oldest, newest, providers = _aggregate(db, Standing, Standing.season == season)
    metas["standings"] = source_meta_from_aggregate(
        "standings",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=season,
        expected_count=team_count or None,
        **common,
    )

    rostered = (
        db.scalar(
            select(func.count(distinct(RosterEntry.player_id))).where(current_roster_clause(season))
        )
        or 0
    )
    spec = spec_for("base")
    base_where = (
        PlayerSeasonStats.season == season,
        PlayerSeasonStats.season_type == regular,
        PlayerSeasonStats.stat_type == "base",
    )
    count, oldest, newest, providers = _aggregate(db, PlayerSeasonStats, *base_where)
    with_stats = (
        db.scalar(
            select(func.count(distinct(PlayerSeasonStats.player_id)))
            .join(RosterEntry, RosterEntry.player_id == PlayerSeasonStats.player_id)
            .where(*base_where, current_roster_clause(season))
        )
        or 0
    )
    meta = source_meta_from_aggregate(
        "player_stats",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=season,
        season_type=regular,
        basis=spec.basis,
        notes=[
            spec.description,
            f"{with_stats} of {rostered} currently rostered players have a base row; "
            "the rest are absent, not zero",
        ],
        **common,
    )
    if rostered:
        meta.expected_count = rostered
        meta.completeness = round(min(with_stats / rostered, 1.0), 4)
    metas["player_stats"] = meta

    count, oldest, newest, providers = _aggregate(
        db,
        TeamSeasonStats,
        TeamSeasonStats.season == season,
        TeamSeasonStats.season_type == regular,
        TeamSeasonStats.stat_type == "base",
    )
    metas["team_stats"] = source_meta_from_aggregate(
        "team_stats",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=season,
        season_type=regular,
        expected_count=team_count or None,
        **common,
    )

    count, oldest, newest, providers = _aggregate(db, Game, Game.season == season)
    metas["games"] = source_meta_from_aggregate(
        "games",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=season,
        season_type=regular,
        observed_through=last_game,
        notes=["games ingested so far; a season's full schedule is not asserted"],
        **common,
    )

    league_year = settings.cap_league_year
    count, oldest, newest, providers = _aggregate(
        db, ContractYear, ContractYear.season == league_year
    )
    with_salary = (
        db.scalar(
            select(func.count(distinct(Contract.player_id)))
            .select_from(Contract)
            .join(ContractYear, ContractYear.contract_id == Contract.id)
            .join(RosterEntry, RosterEntry.player_id == Contract.player_id)
            .where(ContractYear.season == league_year, current_roster_clause(season))
        )
        or 0
    )
    meta = source_meta_from_aggregate(
        "contracts",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=league_year,
        notes=[
            "contract data is a user-supplied snapshot; it is not a trade-legality engine"
        ],
        **common,
    )
    if rostered and count:
        meta.expected_count = rostered
        meta.completeness = round(min(with_salary / rostered, 1.0), 4)
        meta.notes.append(
            f"{with_salary} of {rostered} currently rostered players have a {league_year} salary"
        )
    metas["contracts"] = meta

    count, oldest, newest, providers = _aggregate(db, Injury)
    meta = source_meta_from_aggregate(
        "injuries",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        notes=[
            "no injury provider is configured: availability is not known, and is never "
            "inferred from absence"
            if not settings.injury_data_provider
            else "injury reports describe the present and age in hours"
        ],
        **common,
    )
    metas["injuries"] = meta

    metas["historical_stats"] = _historical_stats_meta(db, season, ctx, now)
    # Rosters for a season the tools are not reading yet are staged by their own job.
    _attach_refresh(
        db,
        metas,
        rosters_job="sync_rosters" if season == settings.current_season else "prepare_league_rosters",
    )
    return metas


def _historical_stats_meta(
    db: Session, analysis_season: str, ctx: SeasonContext, now: datetime
) -> SourceMeta:
    """The completed seasons behind the model window and the trade corpus, as one dataset.

    Judged season by season (each against its own end date), then summarised: ``finalized`` only if
    every season is, otherwise ``stale`` naming the ones that were retrieved before they settled.
    """
    settings = get_settings()
    regular = SeasonType.REGULAR.value
    seasons = [s for s in settings.corpus_season_list if s < analysis_season]
    per_season: dict[str, SourceMeta] = {}
    total = 0
    for label in seasons:
        count, oldest, newest, providers = _aggregate(
            db,
            PlayerSeasonStats,
            PlayerSeasonStats.season == label,
            PlayerSeasonStats.season_type == regular,
            PlayerSeasonStats.stat_type == "base",
        )
        total += count
        per_season[label] = source_meta_from_aggregate(
            "player_stats",
            count=count,
            oldest=oldest,
            newest=newest,
            providers=providers,
            season=label,
            season_type=regular,
            now=now,
            context=ctx,
        )
    present = {k: m for k, m in per_season.items() if m.present_count}
    unsettled = sorted(k for k, m in present.items() if m.freshness is not Freshness.FINALIZED)
    absent = sorted(set(per_season) - set(present))
    if not present:
        freshness, reason = Freshness.MISSING, "no completed-season statistics are stored"
    elif unsettled:
        freshness = Freshness.STALE
        reason = f"retrieved before the season settled, or undated: {', '.join(unsettled)}"
    else:
        freshness, reason = Freshness.FINALIZED, "every stored completed season was retrieved after it settled"
    oldest_all = [m.oldest_retrieved_at for m in present.values() if m.oldest_retrieved_at]
    newest_all = [m.retrieved_at for m in present.values() if m.retrieved_at]
    notes = [f"{len(present)} of {len(seasons)} corpus seasons have base statistics"]
    if absent:
        notes.append(f"no statistics stored for: {', '.join(absent)}")
    return SourceMeta(
        dataset="historical_stats",
        providers=sorted({p for m in present.values() for p in m.providers}),
        upstream="; ".join(sorted({m.upstream for m in present.values()})) or "none",
        retrieved_at=max(newest_all) if newest_all else None,
        oldest_retrieved_at=min(oldest_all) if oldest_all else None,
        freshness=freshness,
        age_seconds=int((now - min(oldest_all)).total_seconds()) if oldest_all else None,
        present_count=len(present),
        expected_count=len(seasons) or None,
        completeness=round(len(present) / len(seasons), 4) if seasons else None,
        scope_state="completed",
        status_reason=reason,
        cadence="once, then re-verified",
        notes=notes,
    )


def rollover_readiness(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Is the database ready for the tools to move to the league's own season?

    The tools switch season on the calendar (``Settings.current_season``); the data has to be in
    place by then. Read-only: it reports what exists for the league season and what is missing.
    """
    settings = get_settings()
    now = now or datetime.now(UTC)
    ctx = season_context(today_in_league(now), grace_days=settings.season_rollover_grace_days)
    league = ctx.league_season.label
    regular = SeasonType.REGULAR.value
    rosters = db.scalar(
        select(func.count(RosterEntry.id)).where(current_roster_clause(league))
    ) or 0
    player_rows = db.scalar(
        select(func.count(PlayerSeasonStats.id)).where(
            PlayerSeasonStats.season == league, PlayerSeasonStats.season_type == regular,
            PlayerSeasonStats.stat_type == "base",
        )
    ) or 0
    team_rows = db.scalar(
        select(func.count(TeamSeasonStats.id)).where(
            TeamSeasonStats.season == league, TeamSeasonStats.season_type == regular,
            TeamSeasonStats.stat_type == "base",
        )
    ) or 0
    impact = db.scalar(
        select(func.count(PlayerImpactEstimate.id)).where(PlayerImpactEstimate.season == league)
    ) or 0
    checks: dict[str, dict[str, Any]] = {
        "rosters_for_league_season": {"present": rosters > 0, "rows": rosters},
        "regular_season_player_stats": {
            "present": player_rows > 0,
            "rows": player_rows,
            "expected_yet": ctx.regular_season_stats_expected,
        },
        "regular_season_team_stats": {
            "present": team_rows > 0,
            "rows": team_rows,
            "expected_yet": ctx.regular_season_stats_expected,
        },
        "impact_estimates": {
            "present": impact > 0,
            "rows": impact,
            "note": "produced by `make train && make score`; run it before the rollover date",
        },
    }
    # The cap year is configuration (CAP_LEAGUE_YEAR) and the league year turns over on 1 July, so it
    # is the one season setting that still needs a hand edit; make it impossible to miss.
    cap_year = settings.cap_league_year
    cap_rows = db.scalar(
        select(func.count(LeagueCapParameters.id)).where(LeagueCapParameters.league_year == league)
    ) or 0
    checks["cap_parameters_for_league_year"] = {
        "present": cap_year == league and cap_rows > 0,
        "configured_cap_league_year": cap_year,
        "league_year": league,
        "cap_rows_for_league_year": cap_rows,
        "note": "set CAP_LEAGUE_YEAR and add a cap_rules YAML (`make seed-config`) when the league "
        "year turns over on 1 July",
    }
    pinned = bool(settings.pinned_season.strip())
    blockers = [
        name
        for name, c in checks.items()
        if not c["present"] and c.get("expected_yet", True)
    ]
    return {
        "season_context": ctx.describe(),
        "pinned_override": settings.pinned_season.strip() or None,
        "pinned_behind_calendar": bool(
            pinned and Season.parse(settings.pinned_season.strip()) < ctx.analysis_season
        ),
        "tools_read_season": settings.current_season,
        "league_season": league,
        "checks": checks,
        "missing_before_rollover": blockers if ctx.analysis_is_baseline or pinned else [],
        "ready": not blockers,
    }


def provider_registry(db: Session) -> list[dict[str, Any]]:
    """Providers Pivot can read from. Never includes a credential, key prefix or key length."""
    settings = get_settings()
    rows = db.execute(
        select(ProviderEntityId.provider, func.count(ProviderEntityId.id)).group_by(
            ProviderEntityId.provider
        )
    ).all()
    links: dict[str, int] = {provider: int(count) for provider, count in rows}
    contract_provider = get_contract_provider()
    return [
        {
            "provider": "nba_api",
            "role": "primary: identity, rosters, standings, season statistics, games",
            "status": "active",
            "licence_note": (
                "The nba_api client is open source; the data it fetches is governed by "
                "NBA.com's Terms of Use. See docs/r8/resource-research.md."
            ),
        },
        {
            "provider": "contracts",
            "role": "secondary: contract and salary snapshot",
            "status": "configured" if contract_provider else "not_configured",
            "configured_provider": getattr(contract_provider, "name", None),
        },
        {
            "provider": "balldontlie",
            "role": "optional: statistics, advanced stats, contracts, injuries (not required)",
            "status": "configured" if settings.balldontlie_configured else "not_configured",
            "tier_setting": settings.balldontlie_tier if settings.balldontlie_configured else None,
            "linked_entities": links.get("balldontlie", 0),
            "live_verified": False,
        },
        {
            "provider": "local_assets",
            "role": "presentation only: team logos and player photos from local folders",
            "status": "active" if db.scalar(select(func.count(MediaAsset.id))) else "empty",
        },
    ]

