"""Request-scoped batching for the salary lookups behind every trade evaluation.

Measured on the live database with `contracts` at **0 rows** — the cheapest possible
case, because a missing contract is one query that returns nothing:

    POST /trades/evaluate  (2-for-2)        61 queries
    POST /trades/generate  (focal BOS)   21,326 queries in 2.15 s

of which 16,640 were the same `SELECT … FROM contracts WHERE player_id = ?` and 1,169
were `SELECT … FROM league_cap_parameters` for one immutable row. With contract data
loaded the plan measured 47,158 queries / 5.17 s on local SQLite; on the compose
Postgres path that is 7–14 s of pure round-trip latency.

The cache lives in `Session.info`, so its lifetime is exactly the request's session —
`get_db` creates one per request — and it is **cleared on every flush and commit**, so a
write inside a session can never be served a stale read. Nothing here is global.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.canonical.rosters import current_roster_clause
from app.db.models import (
    Contract,
    ContractYear,
    DraftPick,
    LeagueCapParameters,
    Player,
    RosterEntry,
)

from .context import PayrollCoverage

#: Historical spelling kept on purpose — this key namespaces a live per-session cache.
_CACHE_KEY = "rosterlab_resolver_cache"


def _cache(db: Session) -> dict[str, Any]:
    cache = db.info.get(_CACHE_KEY)
    if cache is None:
        cache = {
            "cap_params": {},
            "salary": {},
            "payroll": {},
            "roster": {},
            "team_roster": {},
            "player": {},
            "draft_picks": {},
            "financials": {},
            "league_financial_inputs": {},
            "roster_provisional": {},
        }
        db.info[_CACHE_KEY] = cache
    return cache


def reset(db: Session) -> None:
    db.info.pop(_CACHE_KEY, None)


@event.listens_for(Session, "after_flush")
def _clear_on_flush(session: Session, flush_context: Any) -> None:
    session.info.pop(_CACHE_KEY, None)


@event.listens_for(Session, "after_commit")
def _clear_on_commit(session: Session) -> None:
    session.info.pop(_CACHE_KEY, None)


@event.listens_for(Session, "after_rollback")
def _clear_on_rollback(session: Session) -> None:
    session.info.pop(_CACHE_KEY, None)


# ------------------------------------------------------------------ cap parameters


def cap_params_row(db: Session, league_year: str) -> LeagueCapParameters | None:
    """One immutable row per league year, fetched at most once per session.

    `load_cap_params` was called 1,169 times in a single `/trades/generate` request.
    """
    cache = _cache(db)["cap_params"]
    if league_year not in cache:
        cache[league_year] = db.scalar(
            select(LeagueCapParameters).where(LeagueCapParameters.league_year == league_year)
        )
    return cache[league_year]


# ------------------------------------------------------------------------ salaries


def salaries(
    db: Session, player_ids: list[str], league_year: str
) -> dict[str, tuple[int | None, Contract | None]]:
    """`{player_id: (salary_for_league_year, contract)}` for the ids not already cached.

    One query for the contracts and one for their years, rather than two per player. A
    player with no contract is cached as `(None, None)` so the miss is not re-queried —
    which is the whole cost today, since `contracts` is empty.
    """
    cache = _cache(db)["salary"]
    missing = [pid for pid in dict.fromkeys(player_ids) if (pid, league_year) not in cache]
    if missing:
        contracts = list(
            db.scalars(select(Contract).where(Contract.player_id.in_(missing))).all()
        )
        by_player: dict[str, Contract] = {}
        for contract in contracts:
            # Deterministic when a player somehow has more than one contract row: the
            # per-player lookup it replaces took whichever the database returned first.
            existing = by_player.get(contract.player_id)
            if existing is None or contract.id < existing.id:
                by_player[contract.player_id] = contract

        year_by_contract: dict[str, int] = {}
        if contracts:
            for year in db.scalars(
                select(ContractYear).where(
                    ContractYear.contract_id.in_([c.id for c in contracts]),
                    ContractYear.season == league_year,
                )
            ).all():
                year_by_contract[year.contract_id] = year.salary

        for pid in missing:
            found = by_player.get(pid)
            cache[(pid, league_year)] = (
                (year_by_contract.get(found.id), found) if found is not None else (None, None)
            )

    return {pid: cache[(pid, league_year)] for pid in dict.fromkeys(player_ids)}


def financials(db: Session, players: dict[str, str], league_year: str) -> dict[str, Any]:
    """``{player_id: PlayerFinancials}``, batched and memoized per session like ``salaries``.

    The league-wide inputs (the loaded contract horizon, the two-way salary, the recorded unpriced
    rows) are read once per session, not once per call: ``/trades/generate`` builds hundreds of
    trade contexts in one request and must not pay five extra queries for each.
    """
    from app.services import financials as fin  # local: financials does not import the resolver

    cache = _cache(db)["financials"]
    shared = _cache(db)["league_financial_inputs"]
    if league_year not in shared:
        runs = fin.contract_runs(db)  # one read feeds both the horizon and the recorded-unpriced lookup
        shared[league_year] = (
            fin.horizon(db, league_year, runs),
            fin.two_way_salary(db, league_year, cap_params_row(db, league_year)),
            fin.unpriced_observations(db, league_year, runs),
        )
    info, two_way, observed = shared[league_year]
    missing = {pid: name for pid, name in players.items() if (pid, league_year) not in cache}
    if missing:
        computed = fin.player_financials(
            db, missing, league_year, horizon_info=info, two_way=two_way, observed=observed
        )
        for pid, record in computed.items():
            cache[(pid, league_year)] = record
    return {pid: cache[(pid, league_year)] for pid in dict.fromkeys(players)}


def roster_is_provisional(db: Session, season: str) -> bool:
    """Whether the roster snapshot for ``season`` is provisional (camp / offseason), memoized per session."""
    cache = _cache(db)["roster_provisional"]
    if season not in cache:
        # No database read: the calendar names what a roster taken now can honestly be called (the preseason
        # and the offseason are provisional), and a roster that is not the league season's own is a fallback or
        # historical snapshot — also not the final roster of the season being evaluated.
        from app.canonical.seasons import Season
        from app.canonical.tool_seasons import season_context_now

        ctx = season_context_now()
        cache[season] = Season.coerce(season) != ctx.league_season or ctx.roster_basis in (
            "provisional_preseason",
            "offseason_snapshot",
        )
    return cache[season]


def player_salary(
    db: Session, player_id: str, league_year: str
) -> tuple[int | None, Contract | None]:
    return salaries(db, [player_id], league_year)[player_id]


# ------------------------------------------------------------------------- payroll


def team_roster_ids(db: Session, team_id: str, season: str) -> list[str]:
    """The team's current roster, fetched once per (team, season) per request."""
    cache = _cache(db)["team_roster"]
    key = (team_id, season)
    if key not in cache:
        cache[key] = [
            entry.player_id
            for entry in db.scalars(
                select(RosterEntry).where(
                    RosterEntry.team_id == team_id, current_roster_clause(season)
                )
            ).all()
        ]
    return cache[key]


def team_contract_types_known(db: Session, team_id: str, season: str, league_year: str) -> bool:
    """Whether every player on the roster has a KNOWN contract type.

    The 15-standard roster limit is a property of the whole roster, so knowing the type of
    the one player being traded establishes nothing about the other fifteen. Gating
    `ROSTER_SIZE` on the traded players alone would have let a single curated row promote
    a whole roster's verdict from `(warning, medium)` to `(pass, high)`.
    """
    player_ids = team_roster_ids(db, team_id, season)
    if not player_ids:
        return False
    resolved = salaries(db, player_ids, league_year)
    return all(
        contract is not None and contract.contract_type is not None
        for _, contract in resolved.values()
    )


def team_payroll(db: Session, team_id: str, season: str, league_year: str) -> PayrollCoverage:
    """The team's priced salary total **and what it was built from**, memoized per team.

    R2c: the sum is no longer discarded when coverage is partial. `PayrollCoverage.known`
    is a lower bound on payroll and is safe to show with its coverage attached;
    `PayrollCoverage.verified` is still `None` unless every rostered player is priced,
    and that is what every rule reads. The all-or-nothing rule was right about verdicts
    and wrong about disclosure, so only the disclosure changed.
    """
    cache = _cache(db)["payroll"]
    key = (team_id, season, league_year)
    if key not in cache:
        player_ids = team_roster_ids(db, team_id, season)
        resolved = salaries(db, player_ids, league_year)
        known_salaries = [
            salary for pid in player_ids if (salary := resolved[pid][0]) is not None
        ]
        cache[key] = PayrollCoverage(
            known=sum(known_salaries),
            players_known=len(known_salaries),
            players_total=len(player_ids),
        )
    return cache[key]


# ------------------------------------------------------------------ roster identity


def current_roster(db: Session, season: str) -> dict[str, set[str]]:
    """`{player_id: {team_id, …}}` over the current-season rosters, fetched once.

    `build_trade_context` re-ran the membership query for every trade it validated —
    409 identical queries inside one `/trades/generate` request, which evaluates 400
    candidate trades against the same rosters.
    """
    cache = _cache(db)["roster"]
    if season not in cache:
        membership: dict[str, set[str]] = {}
        for entry in db.scalars(
            select(RosterEntry).where(current_roster_clause(season))
        ).all():
            membership.setdefault(entry.player_id, set()).add(entry.team_id)
        cache[season] = membership
    return cache[season]


def players(db: Session, player_ids: list[str]) -> dict[str, Player]:
    """Batched `Player` lookup. `build_trade_context` called `db.get(Player, …)` once per
    move — 769 queries per generate request."""
    cache = _cache(db)["player"]
    missing = [pid for pid in dict.fromkeys(player_ids) if pid not in cache]
    if missing:
        for player in db.scalars(select(Player).where(Player.id.in_(missing))).all():
            cache[player.id] = player
        for pid in missing:
            cache.setdefault(pid, None)
    return {pid: cache[pid] for pid in dict.fromkeys(player_ids) if cache.get(pid) is not None}


def draft_pick_rows(db: Session) -> list[DraftPick]:
    """Every reconciled draft-pick row, in one query, memoized per session.

    `generate_candidates` builds a trade context per candidate; without this the table
    would be re-read once per candidate for a set of rows that cannot change inside a
    request.
    """
    cache = _cache(db)["draft_picks"]
    if "all" not in cache:
        cache["all"] = list(db.scalars(select(DraftPick).order_by(DraftPick.draft_year)).all())
    return cache["all"]
