"""R9.3A — one place where contract and financial facts are assembled.

Cap Center, the Trade Evaluator and Player Explorer all need the same thing: *what does Pivot
actually know about this player's contract, for which season, from which source, and how sure
is it?* They previously each asked ``Contract`` / ``ContractYear`` their own way. This module is
the single read path, so the three tools cannot disagree about a salary, a season or a source.

Everything here is read-only and is built from rows already persisted by the canonical ingestion
jobs (``app.ingestion.balldontlie_jobs``); it never calls a provider.

Vocabulary, kept distinct everywhere it is shown:

* **cap hit** — the amount counted against the salary cap for the season. This is what Pivot's
  ``ContractYear.salary`` holds for BALLDONTLIE rows (``salary_basis == "cap_hit"``).
* **base salary** and **total cash** — different quantities the provider reports; stored, shown
  next to the cap hit, never substituted for it.
* **financial season** — the league year salaries are read for (``CAP_LEAGUE_YEAR``). It is *not*
  the statistics season and not necessarily the roster season, and every record names it.

Unknown stays unknown. A player with no priced year for the financial season is ``unpriced`` /
``no_contract``; he is never a $0 contract, and his base salary or cash is never promoted to a cap
hit. A team's "known salary subtotal" is the sum of the cap hits on file and is never presented as
the team's payroll unless every rostered player is priced (see ``app.cba.context.PayrollCoverage``).

**Contract length.** The provider's per-season endpoint returns one row per contract year, so a
contract's final season can be *derived* from the seasons on file — but only when those seasons
were all loaded for the whole league. :func:`horizon` reads which seasons were (the successful
``sync_contracts`` runs). A contract whose last row is the final season loaded runs **at least**
that long (``through_horizon``); one whose last row is earlier ends there (``ends``); with no
complete horizon the length is ``unknown``. The derivation was checked against the provider's own
contract-aggregate ``free_agent_year`` for 19 sampled players (docs/r9-3a/goat-operationalization.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.records import SINGLE_SEASON_CONTRACT_SOURCE
from app.canonical.seasons import InvalidSeasonError, Season
from app.db.models import Contract, ContractYear, DataSyncRun, LeagueCapParameters

#: The single contract-sync job whose runs define the loaded horizon.
CONTRACT_SYNC_JOB = "sync_contracts"

CAP_HIT_NOTE = (
    "Cap hit is the amount counted against the cap. Base salary and total cash are different "
    "quantities and are shown separately; Pivot never substitutes one for another."
)

#: What the provider does not give, so Pivot cannot show it — stated once, reused by every tool.
NOT_ON_FILE = {
    "options": "Player/team option flags are not loaded: the provider describes them only as free text, "
    "and Pivot does not parse prose into structured options.",
    "guarantees": "Guaranteed amounts are not loaded (they need one provider request per player).",
    "no_trade_clause": "No-trade clauses are not reported by the provider.",
    "cap_holds": "Cap holds, dead money and waived or stretched salary are not on file, so a payroll can be understated.",
    "signing_date": "Signing dates are not reported by the provider, so recently-signed restrictions cannot be checked.",
    "contract_type": "The provider's contract-type vocabulary is inconsistent and is not loaded as a structured field.",
}


def _season_key(label: str) -> int:
    return Season.parse(label).start_year


def _valid(label: str) -> bool:
    try:
        Season.parse(label)
    except InvalidSeasonError:
        return False
    return True


# ------------------------------------------------------------------------------ horizon


@dataclass(frozen=True)
class ContractHorizon:
    """Which contract seasons were loaded for the whole league, starting at the financial season."""

    financial_season: str
    #: Contiguous complete seasons from ``financial_season`` (inclusive), oldest first.
    seasons: tuple[str, ...]
    #: Season label -> ISO timestamp the league-wide load finished.
    loaded_at: dict[str, str] = field(default_factory=dict)
    #: Seasons that were requested but only partially read (some team failed).
    partial: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return bool(self.seasons)

    @property
    def last_season(self) -> str | None:
        return self.seasons[-1] if self.seasons else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "financial_season": self.financial_season,
            "seasons_loaded": list(self.seasons),
            "last_season_loaded": self.last_season,
            "loaded_at": self.loaded_at,
            "partial_seasons": list(self.partial),
            "note": (
                "Seasons after the last one loaded are unknown, not empty: a contract whose final row is the "
                "last season loaded may run longer."
                if self.complete
                else "No complete league-wide contract load covers the financial season, so contract lengths are unknown."
            ),
        }


def contract_runs(db: Session) -> list[DataSyncRun]:
    """Every successful contract sync run, oldest first — read once and shared by the horizon and the
    recorded-unpriced lookup (the resolver memoizes it per request)."""
    return list(
        db.scalars(
            select(DataSyncRun)
            .where(DataSyncRun.job_name == CONTRACT_SYNC_JOB, DataSyncRun.status == "succeeded")
            .order_by(DataSyncRun.started_at)
        ).all()
    )


def horizon(db: Session, financial_season: str, runs: list[DataSyncRun] | None = None) -> ContractHorizon:
    """Contiguous league-wide contract seasons loaded from ``financial_season`` onward."""
    complete: dict[str, str] = {}
    partial: set[str] = set()
    runs = contract_runs(db) if runs is None else runs
    for run in runs:
        detail = run.detail if isinstance(run.detail, dict) else {}
        season = detail.get("season")
        if not isinstance(season, str) or not _valid(season):
            continue
        # A season counts only if the read was clean (no failed team, no rejected or refused row) and returned rows.
        if (
            detail.get("outcome") == "complete"
            and not detail.get("teams_failed")
            and not detail.get("refused_rows_total")
            and detail.get("rows_returned", 1) > 0
        ):
            complete[season] = run.finished_at.isoformat() if run.finished_at else ""
            partial.discard(season)
        elif season not in complete:
            partial.add(season)
    chain: list[str] = []
    try:
        cursor = Season.parse(financial_season)
    except InvalidSeasonError:
        return ContractHorizon(financial_season, ())
    while cursor.label in complete:
        chain.append(cursor.label)
        cursor = cursor.next()
    return ContractHorizon(
        financial_season,
        tuple(chain),
        {s: complete[s] for s in chain},
        tuple(sorted(partial)),
    )


# ------------------------------------------------------------------------------ records


@dataclass(frozen=True)
class SeasonFigures:
    season: str
    cap_hit: int
    base_salary: int | None
    total_cash: int | None
    guaranteed: int | None
    player_option: bool | None
    team_option: bool | None
    basis: str | None
    retrieved_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "cap_hit": self.cap_hit,
            "base_salary": self.base_salary,
            "total_cash": self.total_cash,
            "guaranteed": self.guaranteed,
            "player_option": self.player_option,
            "team_option": self.team_option,
            "salary_basis": self.basis,
        }


@dataclass(frozen=True)
class PlayerFinancials:
    player_id: str
    name: str
    financial_season: str
    status: str  # priced | unpriced | no_contract
    status_reason: str
    seasons: tuple[SeasonFigures, ...]  # financial season onward, oldest first
    end_status: str  # ends | through_horizon | unknown | none
    final_season: str | None
    years_remaining: int | None
    years_remaining_is_lower_bound: bool
    has_gap: bool
    contract_type: str | None
    not_two_way: bool | None
    not_two_way_basis: str | None
    source_provider: str | None
    source_name: str | None
    retrieved_at: datetime | None

    @property
    def current(self) -> SeasonFigures | None:
        for figures in self.seasons:
            if figures.season == self.financial_season:
                return figures
        return None

    @property
    def cap_hit(self) -> int | None:
        current = self.current
        return current.cap_hit if current else None

    def as_dict(self) -> dict[str, Any]:
        current = self.current
        return {
            "player_id": self.player_id,
            "name": self.name,
            "financial_season": self.financial_season,
            "status": self.status,
            "status_reason": self.status_reason,
            "cap_hit": current.cap_hit if current else None,
            "base_salary": current.base_salary if current else None,
            "total_cash": current.total_cash if current else None,
            "salary_basis": current.basis if current else None,
            "seasons": [s.as_dict() for s in self.seasons],
            "contract_end": {
                "status": self.end_status,
                "final_season": self.final_season,
                "years_remaining": self.years_remaining,
                "years_remaining_is_lower_bound": self.years_remaining_is_lower_bound,
                "has_gap": self.has_gap,
                "basis": _end_basis(self.end_status, self.final_season),
            },
            "contract_type": self.contract_type,
            "not_two_way": self.not_two_way,
            "not_two_way_basis": self.not_two_way_basis,
            "source": {
                "provider": self.source_provider,
                "name": self.source_name,
                "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            },
        }


def _end_basis(end_status: str, final_season: str | None) -> str:
    if end_status == "ends":
        return f"last contract year on file is {final_season}; later seasons were loaded and hold no row for this player"
    if end_status == "through_horizon":
        return f"a contract row exists through {final_season}, the last season loaded — it may run longer"
    if end_status == "none":
        return "no contract on file"
    return "contract length is unknown (later seasons were not loaded for the whole league)"


def unpriced_observations(
    db: Session, financial_season: str, runs: list[DataSyncRun] | None = None
) -> dict[str, dict[str, Any]]:
    """Provider rows for ``financial_season`` that carried **no cap hit**, as recorded by the sync run.

    The contract job never writes such a row (a missing cap hit is not a salary), but it lists them
    in the run's ``detail`` with the figures the provider did give. The list is capped by the job
    (80 players), so absence here means "not recorded", never "the provider had no row".
    """
    out: dict[str, dict[str, Any]] = {}
    runs = contract_runs(db) if runs is None else runs
    for run in runs:  # later runs override earlier ones
        detail = run.detail if isinstance(run.detail, dict) else {}
        if detail.get("season") != financial_season:
            continue
        for row in detail.get("unpriced_players") or []:
            if isinstance(row, dict) and row.get("player_id"):
                out[str(row["player_id"])] = {
                    "base_salary": row.get("base_salary"),
                    "total_cash": row.get("total_cash"),
                    "observed_at": run.finished_at.isoformat() if run.finished_at else None,
                }
    return out


def later_unpriced_seasons(
    db: Session, financial_season: str, runs: list[DataSyncRun] | None = None
) -> dict[str, list[str]]:
    """``{player_id: [seasons after the financial season]}`` the provider lists WITHOUT a cap hit.

    Such a row proves the contract runs at least that long even though no cap hit was stored for it, so the
    contract's length cannot be derived from the stored rows (round-1 finding: it read as "ends")."""
    out: dict[str, list[str]] = {}
    floor = _season_key(financial_season)
    for run in contract_runs(db) if runs is None else runs:
        detail = run.detail if isinstance(run.detail, dict) else {}
        season = detail.get("season")
        if not isinstance(season, str) or not _valid(season) or _season_key(season) <= floor:
            continue
        for row in detail.get("unpriced_players") or []:
            if isinstance(row, dict) and row.get("player_id"):
                seasons = out.setdefault(str(row["player_id"]), [])
                if season not in seasons:
                    seasons.append(season)
    for seasons in out.values():
        seasons.sort(key=_season_key)
    return out


def two_way_salary(
    db: Session, financial_season: str, row: LeagueCapParameters | None = None
) -> int | None:
    """The league's two-way salary for the season, from the sourced cap-parameter row.

    ``row`` may be passed in (the resolver already holds it per request); read here otherwise."""
    if row is None:
        row = db.scalar(select(LeagueCapParameters).where(LeagueCapParameters.league_year == financial_season))
    extras = (row.extras if row and isinstance(row.extras, dict) else {}) or {}
    value = extras.get("two_way_salary")
    return int(value) if isinstance(value, int | float) and value > 0 else None


# ------------------------------------------------------------------------------ assembly


def _years_by_contract(
    db: Session, contract_ids: list[str], from_season: str
) -> dict[str, list[ContractYear]]:
    out: dict[str, list[ContractYear]] = {}
    if not contract_ids:
        return out
    floor = _season_key(from_season)
    for year in db.scalars(select(ContractYear).where(ContractYear.contract_id.in_(contract_ids))).all():
        if _valid(year.season) and _season_key(year.season) >= floor:
            out.setdefault(year.contract_id, []).append(year)
    for years in out.values():
        years.sort(key=lambda y: _season_key(y.season))
    return out


def player_financials(
    db: Session,
    players: dict[str, str],
    financial_season: str,
    *,
    horizon_info: ContractHorizon | None = None,
    two_way: int | None = None,
    observed: dict[str, dict[str, Any]] | None = None,
    later_unpriced: dict[str, list[str]] | None = None,
) -> dict[str, PlayerFinancials]:
    """``{player_id: PlayerFinancials}`` for ``players`` (``id -> display name``), in two queries.

    The three league-wide inputs may be passed in (the resolver memoizes them per request); read
    here otherwise.
    """
    info = horizon_info or horizon(db, financial_season)
    if two_way is None:
        two_way = two_way_salary(db, financial_season)
    if observed is None:
        observed = unpriced_observations(db, financial_season)
    if later_unpriced is None:
        later_unpriced = later_unpriced_seasons(db, financial_season)
    ids = list(dict.fromkeys(players))
    contracts: dict[str, Contract] = {}
    if ids:
        for contract in db.scalars(select(Contract).where(Contract.player_id.in_(ids))).all():
            existing = contracts.get(contract.player_id)
            # Deterministic when a player has more than one contract row: the lowest id wins,
            # which is what ``app.cba.resolver.salaries`` does, so the tools agree.
            if existing is None or contract.id < existing.id:
                contracts[contract.player_id] = contract
    years = _years_by_contract(db, [c.id for c in contracts.values()], financial_season)

    out: dict[str, PlayerFinancials] = {}
    for pid in ids:
        out[pid] = _assemble(
            players[pid], pid, contracts.get(pid), years, financial_season, info, two_way, observed.get(pid),
            later_unpriced.get(pid),
        )
    return out


def _assemble(
    name: str,
    player_id: str,
    contract: Contract | None,
    years: dict[str, list[ContractYear]],
    financial_season: str,
    info: ContractHorizon,
    two_way: int | None,
    observed: dict[str, Any] | None = None,
    later_unpriced: list[str] | None = None,
) -> PlayerFinancials:
    if contract is None:
        status, reason = "no_contract", "no contract row on file for this player"
        if observed is not None:
            status, reason = "unpriced", _observed_reason(observed, financial_season, two_way)
        return PlayerFinancials(
            player_id, name, financial_season, status, reason, (), "none", None, None, False, False,
            None, None, None, None, None, None,
        )  # fmt: skip
    rows = years.get(contract.id, [])
    figures = tuple(
        SeasonFigures(
            season=y.season,
            cap_hit=y.salary,
            base_salary=y.base_salary,
            total_cash=y.total_cash,
            guaranteed=y.guaranteed,
            player_option=y.player_option,
            team_option=y.team_option,
            basis=y.salary_basis,
            retrieved_at=y.source_retrieved_at,
        )
        for y in rows
    )
    current = next((f for f in figures if f.season == financial_season), None)
    # Freshness is the financial season's own retrieval: a later load of another season does not make
    # this season's figure newer.
    # An unpriced record has no financial-season figure to be fresh about: its retrieval time is null, not the time a
    # later season was loaded (round 1: Devin Carter showed the 2027-28 load time for an unpriced 2026-27 figure).
    retrieved = current.retrieved_at if current is not None else None
    if current is None:
        status, reason = "unpriced", f"a contract is on file but it has no {financial_season} cap hit"
        if observed is not None:
            reason = _observed_reason(observed, financial_season, two_way)
        if figures:  # later seasons carry cap hits: this is not a two-way-only deal, so drop any two-way hint
            reason = reason.split(". The cash figure equals")[0] + (
                f"; later seasons do carry cap hits (from {figures[0].season}), so a standard contract is likely"
            )
    else:
        status, reason = "priced", f"{financial_season} cap hit on file"

    end_status, final, remaining, lower_bound, gap = _length(figures, financial_season, info, contract)
    if later_unpriced and end_status in ("ends", "through_horizon"):
        # The provider lists a later season without a cap hit: the contract runs at least that long, so a length
        # derived from the stored rows would be wrong in the "ends" direction.
        end_status, remaining, lower_bound = "unknown", None, False

    not_two_way: bool | None = None
    basis: str | None = None
    if current is not None and two_way is not None and current.cap_hit > two_way:
        not_two_way = True
        basis = (
            f"cap hit {current.cap_hit:,} exceeds the {financial_season} two-way salary {two_way:,} "
            "(a league parameter); a two-way contract is paid exactly that figure, so this is not one"
        )
    return PlayerFinancials(
        player_id=player_id,
        name=name,
        financial_season=financial_season,
        status=status,
        status_reason=reason,
        seasons=figures,
        end_status=end_status,
        final_season=final,
        years_remaining=remaining,
        years_remaining_is_lower_bound=lower_bound,
        has_gap=gap,
        contract_type=contract.contract_type,
        not_two_way=not_two_way,
        not_two_way_basis=basis,
        source_provider=contract.source_provider,
        source_name=contract.source_name,
        retrieved_at=retrieved,
    )


def _observed_reason(observed: dict[str, Any], financial_season: str, two_way: int | None) -> str:
    cash, base = observed.get("total_cash"), observed.get("base_salary")
    figures = []
    if isinstance(cash, int):
        figures.append(f"total cash ${cash:,}")
    if isinstance(base, int):
        figures.append(f"base salary ${base:,}")
    text = (
        f"the provider lists a {financial_season} row with no cap hit"
        + (f" ({', '.join(figures)})" if figures else "")
        + "; it is unpriced, not $0"
    )
    if two_way is not None and cash == two_way and base is None:
        text += (
            f". The cash figure equals the {financial_season} two-way salary (${two_way:,}), so a non-counting "
            "two-way deal is likely — unverified, and not assumed"
        )
    return text


def _length(
    figures: tuple[SeasonFigures, ...],
    financial_season: str,
    info: ContractHorizon,
    contract: Contract,
) -> tuple[str, str | None, int | None, bool, bool]:
    """(end_status, final_season, years_remaining, is_lower_bound, has_gap)."""
    if not figures or not any(f.season == financial_season for f in figures):
        return ("none" if not figures else "unknown"), (figures[-1].season if figures else None), None, False, False
    final = figures[-1].season
    # A contract that came from a source other than the per-season endpoint carries its own
    # complete year list (older snapshots): its length is whatever it holds.
    per_season = contract.source_name == SINGLE_SEASON_CONTRACT_SOURCE
    count = len(figures)
    keys = [_season_key(f.season) for f in figures]
    gap = any(b - a != 1 for a, b in zip(keys, keys[1:], strict=False))
    if not per_season:
        return "ends", final, count, False, gap
    # With only the financial season loaded, "the last row is the last season loaded" says nothing
    # about length (every priced contract satisfies it), so the length stays unknown.
    if len(info.seasons) < 2:
        return "unknown", final, None, False, gap
    last_loaded = info.last_season
    assert last_loaded is not None
    if _season_key(final) < _season_key(last_loaded):
        return "ends", final, count, False, gap
    # The final row is the last season loaded: the contract may extend beyond what was read.
    return "through_horizon", final, count, True, gap


# ------------------------------------------------------------------------------ team view


@dataclass(frozen=True)
class TeamFinancials:
    team_id: str
    financial_season: str
    roster_season: str
    roster_size: int
    priced: tuple[PlayerFinancials, ...]
    unpriced: tuple[PlayerFinancials, ...]
    horizon: ContractHorizon

    @property
    def known_subtotal(self) -> int:
        return sum(p.cap_hit or 0 for p in self.priced)

    @property
    def complete(self) -> bool:
        return self.roster_size > 0 and not self.unpriced

    def commitments(self) -> list[dict[str, Any]]:
        """Known cap hits of the *rostered* players per season on file, with how many were summed."""
        by_season: dict[str, dict[str, int]] = {}
        for player in list(self.priced) + list(self.unpriced):
            for figures in player.seasons:
                bucket = by_season.setdefault(figures.season, {"known_total": 0, "players": 0})
                bucket["known_total"] += figures.cap_hit
                bucket["players"] += 1
        return [
            {"season": season, **bucket, "is_financial_season": season == self.financial_season}
            for season, bucket in sorted(by_season.items(), key=lambda kv: _season_key(kv[0]))
        ]


def team_financials(db: Session, team_id: str, financial_season: str, roster_season: str) -> TeamFinancials:
    """Everything Pivot knows about a team's rostered contracts for ``financial_season``."""
    from app.canonical.rosters import current_roster_clause
    from app.db.models import RosterEntry

    entries = db.scalars(
        select(RosterEntry).where(RosterEntry.team_id == team_id, current_roster_clause(roster_season))
    ).all()
    names = {e.player_id: e.player.full_name for e in entries}
    info = horizon(db, financial_season)
    records = player_financials(db, names, financial_season, horizon_info=info)
    priced = tuple(sorted((r for r in records.values() if r.status == "priced"), key=lambda r: -(r.cap_hit or 0)))
    unpriced = tuple(sorted((r for r in records.values() if r.status != "priced"), key=lambda r: r.name))
    return TeamFinancials(team_id, financial_season, roster_season, len(entries), priced, unpriced, info)


_THRESHOLDS = (
    ("salary_cap", "Salary cap"),
    ("luxury_tax", "Luxury-tax line"),
    ("first_apron", "First apron"),
    ("second_apron", "Second apron"),
)


def team_financial_summary(
    fin: TeamFinancials,
    params: dict | None,
    roster_basis: dict | None = None,
) -> dict[str, Any]:
    """The Cap Center's financial block for one team: what is known, what is withheld, and why.

    ``params`` is the financial season's cap-parameter row as a dict (or ``None``). Thresholds are
    returned for that season only; they are never carried to another season.
    """
    priced = len(fin.priced)
    unpriced = len(fin.unpriced)
    total = fin.roster_size
    subtotal = fin.known_subtotal if priced else None
    complete = fin.complete
    provisional = bool(roster_basis and roster_basis.get("provisional"))

    withheld: list[dict[str, str]] = []
    supported: list[str] = []
    cleared: list[str] = []
    position: dict[str, Any] | None = None

    if priced:
        supported.append(f"Cap hit, base salary and cash for {priced} of {total} rostered players ({fin.financial_season}).")
    if fin.horizon.complete and len(fin.horizon.seasons) > 1:
        supported.append(
            f"Contract years through {fin.horizon.last_season} for rostered players with a priced {fin.financial_season} cap hit."
        )

    if complete and params:
        payroll = fin.known_subtotal
        position = {
            "payroll": payroll,
            "room_below_cap": max(params["salary_cap"] - payroll, 0),
            "over_cap_by": max(payroll - params["salary_cap"], 0),
            "caveat": (
                "Computed from the cap hits on file for the current roster snapshot; it excludes cap holds, dead money and "
                "anything not on file, and a provisional roster is not the final roster."
            ),
            "status": (
                "above_second_apron" if payroll > params["second_apron"]
                else "above_first_apron" if payroll > params["first_apron"]
                else "above_tax" if payroll > params["luxury_tax"]
                else "above_cap" if payroll > params["salary_cap"]
                else "below_cap"
            ),
        }
        supported.append("Payroll and apron position: every rostered player has a priced contract.")
    else:
        if params and priced:
            cleared = [label for key, label in _THRESHOLDS if fin.known_subtotal > params[key]]
        reason = (
            f"{unpriced} of {total} rostered players have no {fin.financial_season} cap hit on file"
            if unpriced
            else "there is no current roster on file"
        )
        withheld.append({"conclusion": "Team payroll", "reason": f"{reason}; the sum of the others is a floor, not the payroll."})
        withheld.append({"conclusion": "Cap space", "reason": f"Needs every salary: {reason}."})
        withheld.append({"conclusion": "Apron position", "reason": f"A floor can prove a team is over a line, never under it ({reason})."})
    withheld.append(
        {
            "conclusion": "Luxury-tax bill",
            "reason": "Not computed: the tax is assessed on the end-of-season payroll with graduated rates, and Pivot holds neither cap holds, dead money, nor in-season moves.",
        }
    )
    withheld.append(
        {
            "conclusion": "Seasons after " + (fin.financial_season),
            "reason": "Cap thresholds are not published for later seasons, so later totals are commitments on file, not a cap or apron position.",
        }
    )

    # The OLDEST financial-season figure behind the team's subtotal: "how fresh is this number" is its weakest part.
    retrieved = min((p.retrieved_at for p in fin.priced if p.retrieved_at), default=None)
    return {
        "financial_season": fin.financial_season,
        "roster_season": fin.roster_season,
        "roster_basis": roster_basis,
        "roster_is_provisional": provisional,
        "coverage": {
            "roster_size": total,
            "priced": priced,
            "unpriced": unpriced,
            "share": round(priced / total, 4) if total else None,
            "complete": complete,
        },
        # The label is part of the number: "Payroll" only when every rostered player is priced.
        "subtotal_label": "Payroll" if complete else "Known salary subtotal",
        "known_subtotal": subtotal,
        "payroll": fin.known_subtotal if complete else None,
        "is_floor": bool(priced) and not complete,
        "thresholds_cleared_by_known_subtotal": cleared,
        "thresholds": (
            {
                "season": fin.financial_season,
                **{k: params[k] for k, _ in _THRESHOLDS},
                "source_name": params.get("source_name"),
                "source_url": params.get("source_url"),
                "verified_at": params.get("verified_at"),
            }
            if params
            else None
        ),
        "position": position,
        "unpriced_players": [
            {"player_id": p.player_id, "name": p.name, "status": p.status, "reason": p.status_reason}
            for p in fin.unpriced
        ],
        "commitments": fin.commitments(),
        "commitments_note": (
            "Known cap hits of the players on this roster only, season by season. They exclude cap holds, "
            "draft picks, dead money and players not currently on the roster, and are never a payroll."
        ),
        "horizon": fin.horizon.as_dict(),
        "supported": supported,
        "withheld": withheld,
        "definitions": CAP_HIT_NOTE,
        "not_on_file": NOT_ON_FILE,
        "retrieved_at": retrieved.isoformat() if retrieved else None,
    }
