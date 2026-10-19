from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.impact import TEI_BAND_CALIBRATION
from app.api.schemas import PlayerOut, Provenance, describe_providers
from app.canonical.provenance import build_source_meta
from app.canonical.rosters import current_roster_clause, current_team_of, resolve_roster_season
from app.canonical.seasons import SeasonType
from app.canonical.stats import UnknownStatTypeError, spec_for
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
    PlayerSeasonStats,
    RosterEntry,
    Team,
)
from app.integrations.contracts import get_contract_provider
from app.services.impact_reference import impact_reference

router = APIRouter(prefix="/players", tags=["players"])


def _current_team(db: Session, player_id: str, roster_season: str | None = None) -> Team | None:
    """The player's current team (R8.2: on the *roster* season), through the shared roster definition.

    A player who appears on two current rosters is a contradiction, not a coin flip: this
    returns ``None`` for him (the detail endpoint reports ``membership_status: conflict``).
    Pass ``roster_season`` when looping, so the season is resolved once rather than per row.
    """
    return current_team_of(db, player_id, roster_season, explain_absence=False).team


def _player_out(db: Session, player: Player, roster_season: str | None = None) -> PlayerOut:
    team = _current_team(db, player.id, roster_season)
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
    roster_season = resolve_roster_season(db)
    stmt = select(Player)
    if active_only:
        stmt = stmt.where(Player.is_active)
    if q:
        stmt = stmt.where(func.lower(Player.full_name).contains(q.lower()))
    if team_id or rostered_only:
        roster_stmt = select(RosterEntry.player_id).where(
            current_roster_clause(roster_season)
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
        # R8.2: `current_team` on every player is his team in this roster season.
        "roster_season": roster_season,
        "players": [_player_out(db, p, roster_season).model_dump() for p in players],
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

    membership = current_team_of(db, player.id)
    seasons = tool_seasons(db, roster_season=membership.season)
    return {
        "player": _player_out(db, player, membership.season).model_dump(),
        "membership_status": membership.status,
        "roster_season": membership.season,
        "season_context": seasons,
        "data_status": {"roster": membership.meta.model_dump(mode="json")},
        "impact": {
            "tei": round(impact.tei, 2),
            "tei_offense": round(impact.tei_offense, 2) if impact.tei_offense is not None else None,
            "tei_defense": round(impact.tei_defense, 2) if impact.tei_defense is not None else None,
            # Nominally a 10-90% band. R9.1 measured its coverage at ~57-61% out of time, so it is
            # served with that fact beside it (the key is unchanged: existing readers keep working).
            "tei_range_10_90": [round(impact.tei_low, 2), round(impact.tei_high, 2)]
            if impact.tei_low is not None and impact.tei_high is not None
            else None,
            "tei_range_calibration": TEI_BAND_CALIBRATION,
            "availability": round(impact.availability, 3)
            if impact.availability is not None
            else None,
            "minutes_estimate": impact.minutes_estimate,
            "model": f"{impact_model.algorithm} ({impact_model.version})" if impact_model else None,
            # R8.2: the season whose statistics this estimate was built from — not the roster
            # season, and not the team he plays for now.
            "estimate_season": impact.season,
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
        .where(
            PlayerSeasonStats.player_id == player.id,
            # Regular season only. The key below is (season, stat_type); a playoff row would
            # otherwise overwrite the regular-season figures it shares that key with.
            PlayerSeasonStats.season_type == SeasonType.REGULAR.value,
        )
        .order_by(PlayerSeasonStats.season)
    ).all()
    seasons: dict[str, dict] = {}
    providers: set[str] = set()
    definitions: dict[str, dict | None] = {}
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
        if row.stat_type not in definitions:
            try:
                spec = spec_for(row.stat_type)
                definitions[row.stat_type] = {
                    "aggregation": spec.aggregation.value,
                    "basis": spec.basis.value,
                    "minutes_unit": spec.minutes_unit,
                    "description": spec.description,
                }
            except UnknownStatTypeError:
                definitions[row.stat_type] = None  # present but undefined: say so, don't guess
    return {
        "player_id": player.id,
        "seasons": list(seasons.values()),
        # `player_season_stats` holds nba_api rows and user-imported CSV rows side by
        # side, so the label is read off the rows rather than assumed.
        "source": describe_providers(providers),
        "source_providers": sorted(providers),
        # What each stat_type's figures ARE (per-game vs season total, observed vs
        # modelled), and what unit its MIN field is in: 34.8 and 2,265.0 are both "MIN".
        "season_type": SeasonType.REGULAR.value,
        "stat_definitions": definitions,
        "data_status": {
            "player_stats": build_source_meta(
                "player_stats", rows, season_type=SeasonType.REGULAR.value
            ).model_dump(mode="json")
        },
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
            PlayerSeasonStats.season == season,
            PlayerSeasonStats.season_type == SeasonType.REGULAR.value,
            PlayerSeasonStats.stat_type == "totals",
        )
    ).all()
    roster_season = resolve_roster_season(db)
    roster_team: dict[str, str] = {}
    for entry in db.scalars(
        select(RosterEntry).where(current_roster_clause(roster_season))
    ).all():
        roster_team[entry.player_id] = entry.team.abbreviation
    # The team each total was actually accumulated with. `team_abbr` below is the player's
    # *current* roster team; for a past season (or a player traded since) that is a
    # different team, and presenting it beside the totals attributes the production to it.
    abbreviation_by_team_id = {t.id: t.abbreviation for t in db.scalars(select(Team)).all()}
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
                "season_team_abbr": abbreviation_by_team_id.get(row.team_id)
                if row.team_id
                else None,
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
        # R8.2: `team_abbr` is the player's team in this roster season; `season_team_abbr` is the
        # team he accumulated `season`'s totals with. They differ for anyone who moved.
        "roster_season": roster_season,
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
        "season_type": SeasonType.REGULAR.value,
        "data_status": {
            "player_stats": build_source_meta(
                "player_stats", rows, season=season, season_type=SeasonType.REGULAR.value
            ).model_dump(mode="json")
        },
    }
