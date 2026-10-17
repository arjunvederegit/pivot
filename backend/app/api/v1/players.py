from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import PlayerOut, Provenance, describe_providers
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
    PlayerSeasonStats,
    RosterEntry,
    Team,
)
from app.integrations.contracts import get_contract_provider
from app.services.impact_reference import impact_reference

router = APIRouter(prefix="/players", tags=["players"])


def _current_team(db: Session, player_id: str) -> Team | None:
    settings = get_settings()
    entry = db.scalar(
        select(RosterEntry).where(
            RosterEntry.player_id == player_id,
            RosterEntry.season == settings.current_season,
            RosterEntry.is_current,
        )
    )
    return entry.team if entry else None


def _player_out(db: Session, player: Player) -> PlayerOut:
    team = _current_team(db, player.id)
    from app.api.v1.teams import _team_out

    return PlayerOut(
        id=player.id,
        nba_player_id=player.nba_player_id,
        full_name=player.full_name,
        is_active=player.is_active,
        position=player.position,
        birth_date=player.birth_date,
        height_inches=player.height_inches,
        weight_lbs=player.weight_lbs,
        years_experience=player.years_experience,
        current_team=_team_out(team) if team else None,
        provenance=Provenance.of(player),
    )


@router.get("")
def list_players(
    q: str | None = Query(default=None, min_length=2, description="name search"),
    team_id: str | None = None,
    active_only: bool = True,
    rostered_only: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    stmt = select(Player)
    if active_only:
        stmt = stmt.where(Player.is_active)
    if q:
        stmt = stmt.where(func.lower(Player.full_name).contains(q.lower()))
    if team_id or rostered_only:
        roster_stmt = select(RosterEntry.player_id).where(
            RosterEntry.season == settings.current_season, RosterEntry.is_current
        )
        if team_id:
            roster_stmt = roster_stmt.where(RosterEntry.team_id == team_id)
        stmt = stmt.where(Player.id.in_(roster_stmt))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    players = db.scalars(stmt.order_by(Player.full_name).limit(limit).offset(offset)).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "players": [_player_out(db, p).model_dump() for p in players],
    }


@router.get("/impact-distribution")
def get_impact_distribution(db: Session = Depends(get_db)) -> dict:
    """The scored league as a sorted list of served TEI values, with how it was chosen.

    Declared before `/{player_id}`, which would otherwise capture the path."""
    return impact_reference(db)


@router.get("/{player_id}")
def get_player(player_id: str, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    player = db.get(Player, player_id)
    if player is None:
        raise NotFoundError(f"player {player_id} not found")

    impact_model = db.scalar(
        select(ModelVersion).where(
            ModelVersion.model_name == "player_impact", ModelVersion.is_active
        )
    )
    impact = (
        db.scalar(
            select(PlayerImpactEstimate).where(
                PlayerImpactEstimate.player_id == player.id,
                PlayerImpactEstimate.model_version_id == impact_model.id,
            )
        )
        if impact_model
        else None
    )
    archetype = db.scalar(
        select(PlayerArchetype).where(
            PlayerArchetype.player_id == player.id,
            PlayerArchetype.season == settings.current_season,
        )
    )

    comparables = []
    if archetype and impact and impact_model is not None:
        peers = db.scalars(
            select(PlayerArchetype).where(
                PlayerArchetype.season == settings.current_season,
                PlayerArchetype.role_id == archetype.role_id,
                PlayerArchetype.player_id != player.id,
            )
        ).all()
        peer_impacts = {
            r.player_id: r
            for r in db.scalars(
                select(PlayerImpactEstimate).where(
                    PlayerImpactEstimate.player_id.in_([p.player_id for p in peers]),
                    PlayerImpactEstimate.model_version_id == impact_model.id,
                )
            ).all()
        }
        ranked = sorted(
            (p for p in peers if p.player_id in peer_impacts),
            key=lambda p: abs(peer_impacts[p.player_id].tei - impact.tei),
        )[:5]
        for peer in ranked:
            peer_player = db.get(Player, peer.player_id)
            comparables.append(
                {
                    "player_id": peer.player_id,
                    "name": peer_player.full_name if peer_player else "?",
                    "tei": round(peer_impacts[peer.player_id].tei, 2),
                    "archetype": peer.label,
                }
            )

    return {
        "player": _player_out(db, player).model_dump(),
        "impact": {
            "tei": round(impact.tei, 2),
            "tei_offense": round(impact.tei_offense, 2) if impact.tei_offense is not None else None,
            "tei_defense": round(impact.tei_defense, 2) if impact.tei_defense is not None else None,
            "tei_range_10_90": [round(impact.tei_low, 2), round(impact.tei_high, 2)]
            if impact.tei_low is not None and impact.tei_high is not None
            else None,
            "availability": round(impact.availability, 3)
            if impact.availability is not None
            else None,
            "minutes_estimate": impact.minutes_estimate,
            "model": f"{impact_model.algorithm} ({impact_model.version})" if impact_model else None,
            "note": "Pivot Estimated Impact — a portfolio-model estimate, not a "
            "proprietary metric. See /methodology.",
        }
        if impact
        else {
            "note": "No impact estimate — the player is below the 200-minute window, is neither "
            "on a current roster nor listed as active, or the model has not been trained."
        },
        "archetype": {"label": archetype.label, "role_id": archetype.role_id}
        if archetype
        else None,
        "comparables": comparables,
    }


@router.get("/{player_id}/stats")
def get_player_stats(player_id: str, db: Session = Depends(get_db)) -> dict:
    player = db.get(Player, player_id)
    if player is None:
        raise NotFoundError(f"player {player_id} not found")
    rows = db.scalars(
        select(PlayerSeasonStats)
        .where(PlayerSeasonStats.player_id == player.id)
        .order_by(PlayerSeasonStats.season)
    ).all()
    seasons: dict[str, dict] = {}
    providers: set[str] = set()
    for row in rows:
        if row.source_provider:
            providers.add(row.source_provider)
        entry = seasons.setdefault(
            row.season,
            {
                "season": row.season,
                "source_retrieved_at": row.source_retrieved_at.isoformat()
                if row.source_retrieved_at
                else None,
            },
        )
        entry[row.stat_type] = {"GP": row.games_played, "MIN": row.minutes, **(row.stats or {})}
    return {
        "player_id": player.id,
        "seasons": list(seasons.values()),
        # `player_season_stats` holds nba_api rows and user-imported CSV rows side by
        # side, so the label is read off the rows rather than assumed.
        "source": describe_providers(providers),
        "source_providers": sorted(providers),
    }


@router.get("/{player_id}/contract")
def get_player_contract(player_id: str, db: Session = Depends(get_db)) -> dict:
    player = db.get(Player, player_id)
    if player is None:
        raise NotFoundError(f"player {player_id} not found")
    contract = db.scalar(select(Contract).where(Contract.player_id == player.id))
    if contract is None:
        configured = get_contract_provider() is not None
        return {
            "player_id": player.id,
            "available": False,
            "reason": (
                "Contract data unavailable from the configured provider."
                if configured
                else "No contract provider is configured — nba_api does not supply "
                "contract data, and Pivot never invents salaries. "
                "See data/contracts/README.md."
            ),
        }
    years = db.scalars(
        select(ContractYear)
        .where(ContractYear.contract_id == contract.id)
        .order_by(ContractYear.season)
    ).all()
    return {
        "player_id": player.id,
        "available": True,
        "contract_type": contract.contract_type,
        "signed_date": contract.signed_date.isoformat() if contract.signed_date else None,
        "no_trade_clause": contract.no_trade_clause,
        "source_name": contract.source_name,
        "source_date": contract.source_date.isoformat() if contract.source_date else None,
        "years": [
            {
                "season": y.season,
                "salary": y.salary,
                "guaranteed": y.guaranteed,
                "player_option": y.player_option,
                "team_option": y.team_option,
            }
            for y in years
        ],
        "note": "Contract data is user-imported (not NBA.com data); provenance shown above.",
    }


@router.get("/season-totals/{season}")
def list_season_totals(season: str, db: Session = Depends(get_db)) -> dict:
    """Imported season totals (user CSV) for the Player Lab directory: raw totals plus
    safely derived per-game values, with provenance. One request instead of N."""
    rows = db.scalars(
        select(PlayerSeasonStats).where(
            PlayerSeasonStats.season == season, PlayerSeasonStats.stat_type == "totals"
        )
    ).all()
    settings = get_settings()
    roster_team: dict[str, str] = {}
    for entry in db.scalars(
        select(RosterEntry).where(
            RosterEntry.season == settings.current_season, RosterEntry.is_current
        )
    ).all():
        roster_team[entry.player_id] = entry.team.abbreviation
    # R7: one query for every player in the directory, not one per row. The loop used to
    # call `db.get` per row, which is 573 statements on the shipped season and made the
    # cold Player Explorer load the heaviest read in the product.
    by_id = {
        p.id: p
        for p in db.scalars(
            select(Player).where(Player.id.in_({row.player_id for row in rows}))
        ).all()
    }
    imported_at = None
    players = []
    for row in rows:
        player = by_id.get(row.player_id)
        if player is None:
            continue
        imported_at = row.source_retrieved_at or imported_at
        players.append(
            {
                "player_id": player.id,
                "nba_player_id": player.nba_player_id,
                "name": player.full_name,
                "position": player.position,
                "team_abbr": roster_team.get(player.id),
                "gp": row.games_played,
                "totals": {
                    k: v
                    for k, v in (row.stats or {}).items()
                    if k not in ("per_game", "rates", "source_file")
                },
                "per_game": (row.stats or {}).get("per_game", {}),
                "rates": (row.stats or {}).get("rates", {}),
            }
        )
    return {
        "season": season,
        "count": len(players),
        "players": players,
        "available": len(players) > 0,
        # Hardcoded deliberately, and checked: this query filters `stat_type="totals"`,
        # which only the CSV importer writes (the demo seeder uses "csv_totals"), so the
        # label cannot be wrong the way the player/team serializers were.
        "source": "user-imported season totals CSV (raw totals; per-game derived using GP)",
        "imported_at": imported_at.isoformat() if imported_at else None,
        "note": None
        if players
        else "No totals imported for this season — run `make import-stats-csv`.",
    }
