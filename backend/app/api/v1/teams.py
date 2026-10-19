from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.impact import TEI_BAND_CALIBRATION
from app.api.schemas import Provenance, RosterPlayerOut, TeamOut, describe_providers
from app.canonical.provenance import build_source_meta
from app.canonical.records import SINGLE_SEASON_CONTRACT_SOURCE
from app.canonical.rosters import current_roster, current_roster_clause, resolve_roster_season
from app.canonical.seasons import SeasonType
from app.canonical.stat_access import player_stat_rows
from app.canonical.tool_seasons import tool_seasons
from app.config import get_settings
from app.core.errors import NotFoundError
from app.db.base import get_db
from app.db.models import (
    Contract,
    ContractYear,
    ModelVersion,
    Player,
    PlayerArchetype,
    PlayerImpactEstimate,
    RosterEntry,
    Standing,
    Team,
    TeamNeed,
    TeamSeasonStats,
)
from app.services.acquisition import DEFAULT_LIMIT, acquisition_targets
from app.services.financials import player_financials, team_financial_summary, team_financials
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
        # R8.2: `season` above is the statistics season. The roster season is stated beside it.
        "season_context": tool_seasons(db),
    }


@router.get("/{team_id}/roster")
def get_roster(team_id: str, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    team = _get_team(db, team_id)
    snapshot = current_roster(db, team.id, order_by=RosterEntry.position)
    entries = snapshot.entries
    seasons = tool_seasons(db, roster_season=snapshot.season)

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
    # every field below is None when nothing is on file, and none is defaulted. R9.3A: read
    # through the shared financial service so every tool sees the same salary, season and length.
    financials = player_financials(
        db, {e.player_id: e.player.full_name for e in entries}, settings.cap_league_year
    )

    players = []
    retrieved_at = None
    providers: set[str] = set()
    for entry in entries:
        impact = impacts.get(entry.player_id)
        fin = financials[entry.player_id]
        salary = fin.cap_hit
        length_shown = fin.status == "priced" and fin.end_status in ("ends", "through_horizon")
        retrieved_at = entry.source_retrieved_at or retrieved_at
        if entry.source_provider:
            providers.add(entry.source_provider)
        players.append(
            RosterPlayerOut(
                salary=salary,
                contract_years_remaining=fin.years_remaining if length_shown else None,
                contract_type=fin.contract_type,
                salary_season=settings.cap_league_year if fin.status == "priced" else None,
                contract_years_is_lower_bound=fin.years_remaining_is_lower_bound if length_shown else None,
                contract_final_season=fin.final_season if length_shown else None,
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
        # R8.2: `season` keeps naming the statistics season (archetypes, impact estimates);
        # `roster_season` is the season the membership belongs to. They differ in the summer.
        "season": settings.current_season,
        "roster_season": snapshot.season,
        "season_context": seasons,
        "roster": players,
        # R9.2: the per-player tei_low / tei_high are a model-spread band that tested at 57-61% coverage, not 80%.
        "tei_band_calibration": TEI_BAND_CALIBRATION,
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
        select(ContractYear.contract_id)
        .join(Contract, Contract.id == ContractYear.contract_id)
        .where(
            ContractYear.contract_id.in_(contract_ids),
            ContractYear.season >= from_league_year,
            # R8.3: a contract loaded from a per-season endpoint carries ONE season whatever its
            # real length, so "1 season on file" would read as "expiring" for a five-year deal.
            # Its length is unknown, which is None here, not 1.
            Contract.source_name.is_distinct_from(SINGLE_SEASON_CONTRACT_SOURCE),
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
    seasons = tool_seasons(db)
    roster_season = seasons["roster"]["season"]
    basis_differs = roster_season != settings.current_season
    return {
        "team_id": team.id,
        "season": settings.current_season,
        "roster_season": roster_season,
        "season_context": seasons,
        # Needs are percentile rules over the team's statistics *and the roster that produced
        # them*. After a roster turns over they still describe that earlier team.
        "needs_basis": {
            "season": settings.current_season,
            "describes": f"the {settings.current_season} team (its statistics and roster)",
            "matches_current_roster": not basis_differs,
            "note": (
                f"These needs were measured on the {settings.current_season} team. The current "
                f"roster is {roster_season}, so they may not describe it; they are not recomputed "
                "for the new roster."
            )
            if basis_differs
            else None,
        },
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
    seasons = tool_seasons(db, roster_season=snapshot.season)
    # R8.2: a statistic belongs to the team it was earned with. Name that team beside each line, so
    # a player who moved is not presented as having produced this for his new one.
    teams_by_id = {t.id: t for t in db.scalars(select(Team)).all()}
    experience = {
        p.id: p.years_experience
        for p in db.scalars(select(Player).where(Player.id.in_(player_ids))).all()
    }
    attribution = {}
    for pid in player_ids:
        row = by_player.get(pid)
        if row is None:
            continue
        stat_team = teams_by_id.get(row.team_id) if row.team_id else None
        attribution[pid] = {
            "stat_team_id": row.team_id,
            "stat_team_abbreviation": stat_team.abbreviation if stat_team else None,
            "with_current_team": None if row.team_id is None else row.team_id == team.id,
        }
    return {
        "season": settings.current_season,
        "roster_season": snapshot.season,
        "season_context": seasons,
        "stat_attribution": attribution,
        "missing_reasons": {
            pid: (
                "rookie_no_nba_history"
                if experience.get(pid) == 0
                else "no_statistics_row_for_this_season"
            )
            for pid in result.missing_player_ids
        },
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


def _roster_basis(db: Session, roster_season: str) -> dict | None:
    """The roster's season basis, or ``None`` when the database cannot supply it (an older schema lacks columns the
    season context reads: that must not turn the Cap Center into a 500)."""
    from sqlalchemy.exc import OperationalError, ProgrammingError

    try:
        return tool_seasons(db, roster_season=roster_season).get("roster")
    except (OperationalError, ProgrammingError):
        db.rollback()
        return None


@router.get("/{team_id}/cap-outlook")
def get_cap_outlook(team_id: str, db: Session = Depends(get_db)) -> dict:
    """Multi-season payroll picture for Cap Lab. Fully honest: with no contract data
    imported it returns available=False with the exact import instructions."""
    from app.db.models import Contract, ContractYear, LeagueCapParameters
    from app.integrations.contracts import get_contract_provider

    settings = get_settings()
    team = _get_team(db, team_id)
    roster_season = resolve_roster_season(db)
    entries = db.scalars(
        select(RosterEntry).where(
            RosterEntry.team_id == team.id, current_roster_clause(roster_season)
        )
    ).all()
    roster_ids = {e.player_id: e.player.full_name for e in entries}

    # One contract per player, the lowest id — the pick every reader makes (a second row for the same player would
    # otherwise be summed into the chart while the subtotal counts one).
    contracts = []
    seen_players: set[str] = set()
    for row in db.scalars(
        select(Contract).where(Contract.player_id.in_(list(roster_ids))).order_by(Contract.id).limit(500)
    ).all():
        if row.player_id not in seen_players:
            seen_players.add(row.player_id)
            contracts.append(row)
    if not contracts:
        configured = get_contract_provider() is not None
        return {
            "team_id": team.id,
            "available": False,
            "roster_season": roster_season,
            "cap_league_year": settings.cap_league_year,
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

    fin = team_financials(db, team.id, settings.cap_league_year, roster_season)
    by_season: dict[str, dict] = {}
    player_rows = []
    unknown_length = 0
    # One batched read of every year of every contract (this was one query per contract).
    years_by_contract: dict[str, list[ContractYear]] = {}
    if contracts:
        for year_row in db.scalars(
            select(ContractYear).where(ContractYear.contract_id.in_([c.id for c in contracts]))
        ).all():
            years_by_contract.setdefault(year_row.contract_id, []).append(year_row)
    records = {r.player_id: r for r in (*fin.priced, *fin.unpriced)}
    for contract in contracts:
        years = years_by_contract.get(contract.id, [])
        seasons_payload = []
        for year in sorted(years, key=lambda y: y.season):
            bucket = by_season.setdefault(year.season, {"total": 0, "players": 0})
            bucket["total"] += year.salary
            bucket["players"] += 1
            seasons_payload.append(
                {
                    "season": year.season,
                    "salary": year.salary,
                    "base_salary": year.base_salary,
                    "total_cash": year.total_cash,
                    "salary_basis": year.salary_basis,
                    "player_option": year.player_option,
                    "team_option": year.team_option,
                }
            )
        if seasons_payload:
            record = records.get(contract.player_id)
            end_status = record.end_status if record else "unknown"
            final_season = seasons_payload[-1]["season"]
            # R9.3A: the final season is a claim only when the league-wide horizon backs it
            # (see app.services.financials). `expiring` is None when the length is unknown.
            length_known = end_status == "ends"
            if end_status not in ("ends", "through_horizon"):
                unknown_length += 1
            player_rows.append(
                {
                    "player_id": contract.player_id,
                    "name": roster_ids.get(contract.player_id, "?"),
                    "seasons": seasons_payload,
                    "expiring": (final_season == settings.cap_league_year) if length_known else None,
                    "contract_length_known": length_known,
                    "contract_end": record.as_dict()["contract_end"] if record else None,
                    "financials": record.as_dict() if record else None,
                    "no_trade_clause": contract.no_trade_clause,
                    "source_name": contract.source_name,
                    "source_date": contract.source_date.isoformat()
                    if contract.source_date
                    else None,
                    "retrieved_at": record.retrieved_at.isoformat() if record and record.retrieved_at else None,
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
    # R9.3A: "covered" is the number of rostered players with a PRICED financial-season cap hit — the same count the
    # financials block reports — not the number with any contract row (round 1: LAC 17 vs 15 priced).
    covered = len(fin.priced)
    params = (
        {
            "salary_cap": cap_row.salary_cap,
            "luxury_tax": cap_row.luxury_tax,
            "first_apron": cap_row.first_apron,
            "second_apron": cap_row.second_apron,
            "source_name": cap_row.source_name,
            "source_url": cap_row.source_url,
            "verified_at": cap_row.verified_at.isoformat() if cap_row.verified_at else None,
        }
        if cap_row
        else None
    )
    return {
        "team_id": team.id,
        "available": True,
        "cap_league_year": settings.cap_league_year,
        "roster_season": roster_season,
        "roster_size": len(roster_ids),
        "players_with_contracts": covered,
        "complete": fin.complete,
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
        "known salary — partial payrolls are never presented as complete."
        + (
            f" {unknown_length} contract(s) come from a per-season source and later seasons were not "
            "loaded for the whole league, so contract length, options and expiry are not known for them."
            if unknown_length
            else ""
        ),
        "single_season_contracts": unknown_length,
        # R9.3A — the financial picture, assembled by the shared service the Trade Evaluator
        # and Player Explorer also read. Additive: every field above is unchanged.
        "financials": team_financial_summary(
            fin, params, _roster_basis(db, roster_season)
        ),
    }
