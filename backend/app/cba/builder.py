"""Builds a TradeContext from database state for the legality engine."""

from sqlalchemy.orm import Session

from app.canonical.rosters import resolve_roster_season
from app.config import get_settings
from app.core.errors import DataUnavailableError, InvalidTradeError, NotFoundError
from app.db.models import (
    Contract,
    Team,
)
from app.integrations.contracts import get_contract_provider

from . import resolver
from .context import (
    CapParams,
    PayrollCoverage,
    PickAsset,
    PlayerAsset,
    TeamContext,
    TradeContext,
)


def load_cap_params(db: Session, league_year: str) -> CapParams:
    # Memoized per session: this was called 1,169 times for one immutable row in a
    # single `/trades/generate` request (see app/cba/resolver.py).
    row = resolver.cap_params_row(db, league_year)
    if row is None:
        raise DataUnavailableError(
            f"No cap parameters loaded for league year {league_year}; run `make seed-config`."
        )
    return CapParams(
        league_year=row.league_year,
        salary_cap=row.salary_cap,
        luxury_tax=row.luxury_tax,
        first_apron=row.first_apron,
        second_apron=row.second_apron,
        minimum_team_salary=row.minimum_team_salary,
        source_name=row.source_name,
    )


def _player_salary(
    db: Session, player_id: str, league_year: str
) -> tuple[int | None, Contract | None]:
    """Single-player view over the batched resolver. Prefer `player_salaries` in a loop:
    this was 16,640 of the 21,326 queries a `/trades/generate` request issued."""
    return resolver.player_salary(db, player_id, league_year)


def player_salaries(
    db: Session, player_ids: list[str], league_year: str
) -> dict[str, tuple[int | None, Contract | None]]:
    return resolver.salaries(db, player_ids, league_year)


def _team_payroll(db: Session, team_id: str, season: str, league_year: str) -> PayrollCoverage:
    """The team's priced salary total with the coverage it was computed from (R2c).

    `coverage.known` is a lower bound safe to disclose; `coverage.verified` is `None`
    until every rostered player is priced and is what the rules read. Batched and
    memoized per team for the request."""
    return resolver.team_payroll(db, team_id, season, league_year)


def _validate_player_moves(db: Session, player_moves: list[dict], season: str) -> None:
    """Reject trades that cannot exist, before any of them is scored.

    Both checks need a database session, so neither can live in a Pydantic
    `field_validator` — and `trades.py` reconstructs saved trades from stored
    `trade_assets` without touching a request model at all, so a validator there would
    also leave every already-persisted trade unvalidated. `build_trade_context` is the
    one place every path passes through.

    QA-2: a player could be "acquired" by the team that already rosters them, landing in
    `after_cards` twice and producing +0.45 wins for acquiring your own player.
    QA-3: an identical move listed twice was counted twice, flipping
    `aggregates_salaries` and shifting `roster_after` by two.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for move in player_moves:
        player_id = move["player_id"]
        if player_id in seen:
            duplicates.append(player_id)
        seen.add(player_id)
    if duplicates:
        names = _player_names(db, duplicates)
        raise InvalidTradeError(
            "the same player appears in more than one move: " + ", ".join(names)
        )

    if not player_moves:
        return

    membership = resolver.current_roster(db, season)
    phantom: list[str] = []
    for move in player_moves:
        player_id = move["player_id"]
        teams = membership.get(player_id)
        # A player absent from every current roster is an unknown-roster case, not a
        # phantom move: the trade may be legitimate against data we do not hold, and
        # refusing it would block every offseason free agent. Only assert the negative
        # when we can see where the player actually is.
        if teams and move["from_team_id"] not in teams:
            phantom.append(player_id)
    if phantom:
        names = _player_names(db, phantom)
        raise InvalidTradeError(
            "these players are not on the roster they are being traded from: "
            + ", ".join(names)
        )


def _player_names(db: Session, player_ids: list[str]) -> list[str]:
    by_id = resolver.players(db, player_ids)
    return [by_id[pid].full_name if pid in by_id else pid for pid in dict.fromkeys(player_ids)]


def _attach_pick_ownership(db: Session, contexts: dict[str, TeamContext]) -> None:
    """Fill in each team's verified first-round holdings, or leave them `None`.

    Ownership starts from the default every league runs on — a team owns its own picks —
    and applies only the transfers a source has resolved to an owner. A team with **any**
    unresolved entry (a swap, a protection, a conditional conveyance) gets `None`, not a
    partial count: one unreducible clause is enough to make the picture uncertain, and the
    Stepien rule is entitled to know the difference.
    """
    rows = resolver.draft_pick_rows(db)
    if not rows:
        return  # no reconciled source loaded; holdings stay None and the rule says so
    years = sorted({r.draft_year for r in rows})
    unresolved: dict[str, list[dict]] = {}
    conveyed: dict[tuple[str, int], int] = {}
    acquired: dict[tuple[str, int], int] = {}
    for row in rows:
        if row.round_number != 1:
            continue
        if not row.is_verified:
            unresolved.setdefault(row.original_team_id, []).append(
                {
                    "draft_year": row.draft_year,
                    "conveyance": row.conveyance,
                    "source_text": row.source_text,
                }
            )
            continue
        conveyed[(row.original_team_id, row.draft_year)] = (
            conveyed.get((row.original_team_id, row.draft_year), 0) + 1
        )
        acquired[(row.owning_team_id, row.draft_year)] = (
            acquired.get((row.owning_team_id, row.draft_year), 0) + 1
        )
    for team_id, context in contexts.items():
        if team_id in unresolved:
            context.pick_ownership_unresolved = unresolved[team_id]
            continue
        context.first_round_holdings = {
            year: max(1 - conveyed.get((team_id, year), 0), 0)
            + acquired.get((team_id, year), 0)
            for year in years
        }


def build_trade_context(
    db: Session,
    team_ids: list[str],
    player_moves: list[dict],
    pick_moves: list[dict] | None = None,
    league_year: str | None = None,
) -> TradeContext:
    """player_moves: [{player_id, from_team_id, to_team_id}]
    pick_moves: [{from_team_id, to_team_id, draft_year, round_number, protections, is_hypothetical}]
    """
    settings = get_settings()
    league_year = league_year or settings.cap_league_year
    # R8.2: membership is checked against the roster season, not the statistics season.
    season = resolve_roster_season(db)
    params = load_cap_params(db, league_year)

    _validate_player_moves(db, player_moves, season)

    contexts: dict[str, TeamContext] = {}
    for team_id in team_ids:
        team = db.get(Team, team_id)
        if team is None:
            raise NotFoundError(f"Unknown team id {team_id}")
        coverage = _team_payroll(db, team_id, season, league_year)
        contexts[team_id] = TeamContext(
            team_id=team_id,
            abbreviation=team.abbreviation,
            name=team.full_name,
            roster_count_before=coverage.players_total,
            coverage_before=coverage,
            roster_contract_types_known=resolver.team_contract_types_known(
                db, team_id, season, league_year
            ),
        )

    moved = resolver.players(db, [m["player_id"] for m in player_moves])
    resolved = resolver.salaries(db, list(moved), league_year)
    for move in player_moves:
        player = moved.get(move["player_id"])
        if player is None:
            raise NotFoundError(f"Unknown player id {move['player_id']}")
        salary, contract = resolved[player.id]
        asset = PlayerAsset(
            player_id=player.id,
            name=player.full_name,
            from_team_id=move["from_team_id"],
            to_team_id=move["to_team_id"],
            salary=salary,
            contract_type=contract.contract_type if contract else None,
            signed_date=contract.signed_date if contract else None,
            no_trade_clause=contract.no_trade_clause if contract else None,
        )
        if move["from_team_id"] in contexts:
            contexts[move["from_team_id"]].outgoing.append(asset)
        if move["to_team_id"] in contexts:
            contexts[move["to_team_id"]].incoming.append(asset)

    _attach_pick_ownership(db, contexts)

    for move in pick_moves or []:
        pick = PickAsset(
            from_team_id=move["from_team_id"],
            to_team_id=move["to_team_id"],
            draft_year=move["draft_year"],
            round_number=move["round_number"],
            protections=move.get("protections"),
            is_hypothetical=bool(move.get("is_hypothetical", True)),
        )
        if move["from_team_id"] in contexts:
            contexts[move["from_team_id"]].picks_out.append(pick)
        if move["to_team_id"] in contexts:
            contexts[move["to_team_id"]].picks_in.append(pick)

    return TradeContext(
        league_year=league_year,
        params=params,
        teams=[contexts[tid] for tid in team_ids],
        contract_provider_configured=get_contract_provider() is not None,
    )
