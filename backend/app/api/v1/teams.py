from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import Provenance, RosterPlayerOut, TeamOut, describe_providers
from app.canonical.provenance import build_source_meta
from app.canonical.rosters import current_roster, current_roster_clause
from app.canonical.seasons import SeasonType
from app.canonical.stat_access import player_stat_rows
from app.cba import resolver
from app.config import get_settings
from app.core.errors import NotFoundError
from app.db.base import get_db
from app.db.models import (
    ContractYear,
    ModelVersion,
    PlayerArchetype,
    PlayerImpactEstimate,
    RosterEntry,
    Standing,
    Team,
    TeamNeed,
    TeamSeasonStats,
)
from app.services.acquisition import DEFAULT_LIMIT, acquisition_targets
from app.services.payroll import team_payroll_summary

router = APIRouter(prefix="/teams", tags=["teams"])


def _team_out(team: Team) -> TeamOut:
    return TeamOut(
        id=team.id,
        nba_team_id=team.nba_team_id,
        full_name=team.full_name,
        abbreviation=team.abbreviation,
        nickname=team.nickname,
        city=team.city,
        conference=team.conference,
        division=team.division,
        provenance=Provenance.of(team),
    )


def _get_team(db: Session, team_id: str) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise NotFoundError(f"team {team_id} not found")
    return team


@router.get("")
def list_teams(db: Session = Depends(get_db)) -> list[TeamOut]:
    teams = db.scalars(select(Team).order_by(Team.full_name)).all()
    return [_team_out(t) for t in teams]


@router.get("/{team_id}")
def get_team(team_id: str, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    team = _get_team(db, team_id)
    season = settings.current_season
    standing = db.scalar(
        select(Standing).where(Standing.team_id == team.id, Standing.season == season)
    )
    stats: dict[str, dict] = {}
    stats_retrieved_at = None
    stat_rows = db.scalars(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == team.id,
            TeamSeasonStats.season == season,
            # Explicit: a team's playoff line is a different population from its regular season.
            TeamSeasonStats.season_type == SeasonType.REGULAR.value,
        )
    ).all()
    for row in stat_rows:
        stats[row.stat_type] = row.stats or {}
        stats_retrieved_at = row.source_retrieved_at
    team_stat_meta = build_source_meta(
        "team_stats",
        stat_rows,
        season=season,
        season_type=SeasonType.REGULAR.value,
        notes=["per-game averages over the regular season, as published by NBA.com"],
    )
    return {
        "team": _team_out(team).model_dump(),
        "season": season,
        "standing": {
            "wins": standing.wins,
            "losses": standing.losses,
            "win_pct": standing.win_pct,
            "conference": standing.conference,
            "playoff_rank": standing.playoff_rank,
            "details": standing.details,
            "source_retrieved_at": standing.source_retrieved_at.isoformat()
            if standing.source_retrieved_at
            else None,
        }
        if standing
        else None,
        "stats": stats,
        "stats_retrieved_at": stats_retrieved_at.isoformat() if stats_retrieved_at else None,
        "data_status": {"team_stats": team_stat_meta.model_dump(mode="json")},
    }


@router.get("/{team_id}/roster")
def get_roster(team_id: str, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    team = _get_team(db, team_id)
    snapshot = current_roster(db, team.id, order_by=RosterEntry.position)
    entries = snapshot.entries

    impact_model = db.scalar(
        select(ModelVersion).where(
            ModelVersion.model_name == "player_impact", ModelVersion.is_active
        )
    )
    impacts = {}
    if impact_model:
        impacts = {
            r.player_id: r
            for r in db.scalars(
                select(PlayerImpactEstimate).where(
                    PlayerImpactEstimate.model_version_id == impact_model.id
                )
            ).all()
        }
    archetypes = {
        a.player_id: a.label
        for a in db.scalars(
            select(PlayerArchetype).where(PlayerArchetype.season == settings.current_season)
        ).all()
    }

    # Contract facts for the cap league year, batched. Absent contracts stay absent:
    # every field below is None when nothing is on file, and none is defaulted.
    contracts = resolver.salaries(db, [e.player_id for e in entries], settings.cap_league_year)
    contract_years = _remaining_contract_years(
        db, [c.id for _, c in contracts.values() if c is not None], settings.cap_league_year
    )

    players = []
    retrieved_at = None
    providers: set[str] = set()
    for entry in entries:
        impact = impacts.get(entry.player_id)
        salary, contract = contracts[entry.player_id]
        retrieved_at = entry.source_retrieved_at or retrieved_at
        if entry.source_provider:
            providers.add(entry.source_provider)
        players.append(
            RosterPlayerOut(
                salary=salary,
                contract_years_remaining=contract_years.get(contract.id) if contract else None,
                contract_type=contract.contract_type if contract else None,
                player_id=entry.player_id,
                nba_player_id=entry.player.nba_player_id,
                name=entry.player.full_name,
                position=entry.position,
                jersey_number=entry.jersey_number,
                age=entry.age,
                height_inches=entry.player.height_inches,
                years_experience=entry.player.years_experience,
                tei=round(impact.tei, 2) if impact else None,
                tei_low=round(impact.tei_low, 2)
                if impact and impact.tei_low is not None
                else None,
                tei_high=round(impact.tei_high, 2)
                if impact and impact.tei_high is not None
                else None,
                archetype=archetypes.get(entry.player_id),
                availability=round(impact.availability, 3)
                if impact and impact.availability is not None
                else None,
            ).model_dump()
        )
    return {
        "team": _team_out(team).model_dump(),
        "season": settings.current_season,
        "roster": players,
        # Read off the rows, not assumed. A demo-seeded roster says so here.
        "source": describe_providers(providers),
        "source_providers": sorted(providers),
        "source_retrieved_at": retrieved_at.isoformat() if retrieved_at else None,
        # R8: freshness, completeness and the meaning of "on this roster", as data.
        "data_status": {"roster": snapshot.meta.model_dump(mode="json")},
    }


def _remaining_contract_years(
    db: Session, contract_ids: list[str], from_league_year: str
) -> dict[str, int]:
    """Seasons on file at or after `from_league_year`, per contract.

    Counts what the snapshot actually carries — it is not a claim about the contract's
    true remaining length, which no provider here reports. A contract whose file rows end
    at the cap league year returns 1 (expiring), not 0.
    """
    if not contract_ids:
        return {}
    counts: dict[str, int] = {}
    for (contract_id,) in db.execute(
        select(ContractYear.contract_id).where(
            ContractYear.contract_id.in_(contract_ids), ContractYear.season >= from_league_year
        )
    ).all():
        counts[contract_id] = counts.get(contract_id, 0) + 1
    return counts


@router.get("/{team_id}/needs")
def get_needs(team_id: str, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    team = _get_team(db, team_id)
    needs = db.scalars(
        select(TeamNeed)
        .where(TeamNeed.team_id == team.id, TeamNeed.season == settings.current_season)
        .order_by(TeamNeed.severity.desc())
    ).all()
    return {
        "team_id": team.id,
        "season": settings.current_season,
        "computed": bool(needs),
        "note": None
        if needs
        else "Needs not computed yet — run `make score` after ingesting data.",
        "needs": [
            {
                "need_key": n.need_key,
                "severity": n.severity,
                "percentile": n.percentile,
                "explanation": n.explanation,
            }
            for n in needs
        ],
        "method": "Transparent percentile rules over real team statistics and roster "
        "composition (see /methodology); no LLM involvement.",
    }


@router.get(
    "/{team_id}/acquisition-targets",
    summary="Start from a need: who addresses it, what it would cost, and the trade",
    description=(
        "Diagnosis to trade in one call. The chosen need defaults to the most severe one "
        "a player skill can address; candidates are **filtered** to players above this "
        "roster's own level in that skill and **ranked** by projected win change, and "
        "both rules are named in the response.\n\n"
        "Each target is then put through the trade evaluator with a package that balances "
        "its modelled value, and returned only if both sides clear the conditions the "
        "candidate generator already applies. Set `feasible_only=false` to see the "
        "unfiltered ranking — across the 30 ingested rosters that filter takes the number "
        "of distinct players appearing in a top five from 26 to 72."
    ),
)
def team_acquisition_targets(
    team_id: str,
    need_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    sort: str = "impact",
    feasible_only: bool = True,
    scenario_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return acquisition_targets(
        db,
        team_id,
        need_key=need_key,
        limit=limit,
        sort=sort,
        feasible_only=feasible_only,
        scenario_id=scenario_id,
    )


@router.get("/{team_id}/payroll")
def get_payroll(team_id: str, db: Session = Depends(get_db)) -> dict:
    team = _get_team(db, team_id)
    return {"team_id": team.id, **team_payroll_summary(db, team)}


@router.get("/{team_id}/season-stats")
def get_player_season_stats(team_id: str, db: Session = Depends(get_db)) -> dict:
    """Per-player current-season stats for this team's roster (for the team page).

    The figures are regular-season per-game averages (`stat_definition` says so). Rostered
    players with no row are listed in `missing_player_ids` — they are absent, not zero.
    """
    settings = get_settings()
    team = _get_team(db, team_id)
    snapshot = current_roster(db, team.id)
    player_ids = snapshot.player_ids
    result = player_stat_rows(
        db, player_ids, season=settings.current_season, stat_type="base"
    )
    by_player = result.rows
    return {
        "season": settings.current_season,
        "stats": {
            pid: {
                "GP": by_player[pid].games_played,
                "MIN": by_player[pid].minutes,
                **{
                    k: (by_player[pid].stats or {}).get(k)
                    for k in ("PTS", "REB", "AST", "STL", "BLK", "FG3_PCT", "FG_PCT")
                },
            }
            for pid in player_ids
            if pid in by_player
        },
        "missing_player_ids": result.missing_player_ids,
        "stat_definition": {
            "stat_type": result.spec.stat_type,
            "aggregation": result.spec.aggregation.value,
            "basis": result.spec.basis.value,
            "season_type": result.scope.season_type.value,
            "minutes_unit": result.spec.minutes_unit,
        },
        "data_status": {
            "player_stats": result.meta.model_dump(mode="json"),
            "roster": snapshot.meta.model_dump(mode="json"),
        },
    }


@router.get("/{team_id}/cap-outlook")
def get_cap_outlook(team_id: str, db: Session = Depends(get_db)) -> dict:
    """Multi-season payroll picture for Cap Lab. Fully honest: with no contract data
    imported it returns available=False with the exact import instructions."""
    from app.db.models import Contract, ContractYear, LeagueCapParameters
    from app.integrations.contracts import get_contract_provider

    settings = get_settings()
    team = _get_team(db, team_id)
    entries = db.scalars(
        select(RosterEntry).where(
            RosterEntry.team_id == team.id, current_roster_clause(settings.current_season)
        )
    ).all()
    roster_ids = {e.player_id: e.player.full_name for e in entries}

    contracts = db.scalars(
        select(Contract).where(Contract.player_id.in_(list(roster_ids))).limit(500)
    ).all()
    if not contracts:
        configured = get_contract_provider() is not None
        return {
            "team_id": team.id,
            "available": False,
            "reason": (
                "Contract data is configured but no contracts matched this roster yet — "
                "run `make sync-data`."
                if configured
                else "Contract data isn't imported. Download the Basketball-Reference "
                "player-contracts page to data/imports/contracts/players.html, set "
                "CONTRACT_DATA_PROVIDER=bbref_snapshot in .env, then run `make sync-data`."
            ),
            "contract_provider_configured": configured,
        }

    by_season: dict[str, dict] = {}
    player_rows = []
    for contract in contracts:
        years = db.scalars(
            select(ContractYear).where(ContractYear.contract_id == contract.id)
        ).all()
        seasons_payload = []
        for year in sorted(years, key=lambda y: y.season):
            bucket = by_season.setdefault(year.season, {"total": 0, "players": 0})
            bucket["total"] += year.salary
            bucket["players"] += 1
            seasons_payload.append(
                {
                    "season": year.season,
                    "salary": year.salary,
                    "player_option": year.player_option,
                    "team_option": year.team_option,
                }
            )
        if seasons_payload:
            final_season = seasons_payload[-1]["season"]
            player_rows.append(
                {
                    "player_id": contract.player_id,
                    "name": roster_ids.get(contract.player_id, "?"),
                    "seasons": seasons_payload,
                    "expiring": final_season == settings.cap_league_year,
                    "no_trade_clause": contract.no_trade_clause,
                    "source_name": contract.source_name,
                    "source_date": contract.source_date.isoformat()
                    if contract.source_date
                    else None,
                }
            )

    def _current_year_salary(row: dict) -> int:
        seasons: list[dict] = row["seasons"]
        for entry in seasons:
            if entry["season"] == settings.cap_league_year:
                return int(entry["salary"])
        return 0

    player_rows.sort(key=lambda r: -_current_year_salary(r))
    cap_row = db.scalar(
        select(LeagueCapParameters).where(
            LeagueCapParameters.league_year == settings.cap_league_year
        )
    )
    covered = sum(1 for pid in roster_ids if any(c.player_id == pid for c in contracts))
    return {
        "team_id": team.id,
        "available": True,
        "cap_league_year": settings.cap_league_year,
        "roster_size": len(roster_ids),
        "players_with_contracts": covered,
        "complete": covered == len(roster_ids),
        "seasons": [{"season": season, **bucket} for season, bucket in sorted(by_season.items())],
        "players": player_rows,
        "cap_parameters": {
            "salary_cap": cap_row.salary_cap,
            "luxury_tax": cap_row.luxury_tax,
            "first_apron": cap_row.first_apron,
            "second_apron": cap_row.second_apron,
        }
        if cap_row
        else None,
        "note": "Cap/apron position is only computed when every rostered player has a "
        "known salary — partial payrolls are never presented as complete.",
    }
