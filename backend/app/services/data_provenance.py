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

from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.canonical.provenance import SourceMeta, source_meta_from_aggregate
from app.canonical.rosters import current_roster_clause, resolve_season
from app.canonical.seasons import SeasonType
from app.canonical.stats import spec_for
from app.config import get_settings
from app.db.models import (
    Contract,
    ContractYear,
    Game,
    MediaAsset,
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
        p for (p,) in db.execute(select(distinct(model.source_provider)).where(*where)) if p
    ]
    return count, oldest, newest, providers


def dataset_freshness(db: Session) -> dict[str, SourceMeta]:
    settings = get_settings()
    season = resolve_season()
    regular = SeasonType.REGULAR.value
    team_count = db.scalar(select(func.count(Team.id))) or 0

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
    )
    # Rosters are measured against teams covered, not rows: 30 teams is the expectation.
    if team_count:
        meta.present_count = covered_teams
        meta.completeness = round(min(covered_teams / team_count, 1.0), 4)
    metas["rosters"] = meta

    count, oldest, newest, providers = _aggregate(
        db, Standing, Standing.season == season
    )
    metas["standings"] = source_meta_from_aggregate(
        "standings",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=season,
        expected_count=team_count or None,
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
    )

    count, oldest, newest, providers = _aggregate(db, Game, Game.season == season)
    metas["games"] = source_meta_from_aggregate(
        "games",
        count=count,
        oldest=oldest,
        newest=newest,
        providers=providers,
        season=season,
        notes=["games ingested so far; a season's full schedule is not asserted"],
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
    )
    if rostered and count:
        meta.expected_count = rostered
        meta.completeness = round(min(with_salary / rostered, 1.0), 4)
        meta.notes.append(
            f"{with_salary} of {rostered} currently rostered players have a {league_year} salary"
        )
    metas["contracts"] = meta
    return metas


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

