"""BALLDONTLIE -> Pivot's canonical tables (R8.3).

Everything here goes provider adapter -> canonical record -> identity crosswalk -> persistence with
provenance. No tool reads BALLDONTLIE directly; they read the tables these jobs fill.

Rules shared by every job, each of them a deliberate refusal:

* **Idempotent.** A second run over the same responses changes nothing (``unchanged`` counts say
  so); a changed figure is an ``updated`` row, never a duplicate.
* **Non-destructive.** Nothing is deleted. A contract the provider stops listing stays, and is
  counted as ``not_reseen``; an injury it stops listing is *closed* (``valid_to``), which says "no
  longer reported", never "healthy".
* **No silent cross-provider overwrite.** A salary another provider already wrote for the same
  player and season is left alone and filed as a data-quality finding if the figures differ.
* **Unmapped is quarantined, not guessed.** A row whose player has no verified crosswalk entry is
  listed in the run's ``detail`` and written nowhere.
* **Missing is NULL.** A contract year with no cap hit is *not* written as a salary of 0 (or as its
  base salary): it is listed as ``unpriced`` with the figures the provider did give.
* **A partial read is labelled partial.** One failed team in thirty is ``outcome: partial`` and the
  run says which; a capped pagination is never persisted as if it were the whole season.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.identity import ProviderIdMap
from app.canonical.records import (
    SINGLE_SEASON_CONTRACT_SOURCE,
    ContractYearRecord,
    PlayerStatRecord,
)
from app.canonical.rosters import resolve_roster_season
from app.canonical.seasons import Season, SeasonType, StatScope
from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    Contract,
    ContractYear,
    Injury,
    Player,
    ProviderEntityId,
    ProviderStatSnapshot,
)
from app.ingestion.contract_coverage import contract_coverage, roster_side_unmatched, summarize
from app.ingestion.quality import upsert_issue
from app.ingestion.runs import sync_run
from app.integrations.balldontlie.crosswalk import crosswalk_players, crosswalk_teams
from app.integrations.balldontlie.provider import BalldontlieProvider, InvalidStatPairingError

logger = get_logger(__name__)

PROVIDER = "balldontlie"
JOB_IDENTITIES = "sync_provider_identities"
JOB_CONTRACTS = "sync_contracts"
JOB_INJURIES = "sync_injuries"
JOB_STATS = "sync_provider_stats"

#: The salary figure the existing readers (``cba.resolver.salaries``, payroll, Cap Center, the trade
#: evaluator) treat as "the salary". It is the *cap hit* — the only one of the provider's three
#: dollar figures that is the amount counted against the cap — and the row says so.
SALARY_BASIS = "cap_hit"

#: Statistic groups worth holding for R9 and what their figures are *per*. ``per_game`` was verified
#: against NBA.com for ``general/base`` (389 of 389 compared players' PTS equal); the advanced group
#: is season-level rates/ratings, identical to NBA.com's on all 8 compared fields. Everything else
#: is stored as ``unspecified`` — present and labelled, not yet reconciled.
STAT_GROUPS: dict[str, tuple[str, str | None, str]] = {
    # key -> (category, type, aggregation)
    "general/base": ("general", "base", "per_game"),
    "general/advanced": ("general", "advanced", "season_rate"),
    "general/usage": ("general", "usage", "unspecified"),
    "general/scoring": ("general", "scoring", "unspecified"),
    "general/misc": ("general", "misc", "unspecified"),
    "clutch/base": ("clutch", "base", "unspecified"),
    "defense/overall": ("defense", "overall", "unspecified"),
    "shooting/by_zone": ("shooting", "by_zone", "unspecified"),
    "tracking/possessions": ("tracking", "possessions", "unspecified"),
    "hustle": ("hustle", None, "unspecified"),
}
DEFAULT_STAT_GROUPS = ("general/base", "general/advanced")


class ProviderSyncError(RuntimeError):
    """The provider could not give an answer the job can safely act on."""


def _apply(row: Any, values: dict[str, Any]) -> bool:
    """Set only what differs. Returns whether anything changed (so re-runs report ``unchanged``)."""
    changed = False
    for key, value in values.items():
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    return changed


# --------------------------------------------------------------------------- identities


def link_embedded_players(db: Session, records: Iterable[Any]) -> dict[str, Any]:
    """Link players a data row names but ``/players/active`` did not list (retired, free agents,
    last season's roster churn) from the biography the provider embeds in that very row.

    It goes through the same crosswalk as active players, so nothing is linked on a name alone:
    namesakes without separating biography stay ambiguous and are reported.
    """
    ids = ProviderIdMap(db)
    bios = {}
    for rec in records:
        pid = getattr(rec, "player_provider_id", None)
        bio = getattr(rec, "player", None)
        if pid and bio is not None and ids.lookup("player", PROVIDER, pid) is None:
            bios[pid] = bio
    if not bios:
        return {"attempted": 0}
    report = crosswalk_players(db, list(bios.values()), season=resolve_roster_season(db))
    return {
        "attempted": len(bios),
        "linked": report.linked,
        "ambiguous": len(report.ambiguous),
        "unresolved": len(report.unresolved),
        "unresolved_examples": [u["name"] for u in report.unresolved[:10]],
    }


def sync_identities(db: Session, provider: BalldontlieProvider) -> dict[str, Any]:
    """Link BALLDONTLIE's 30 active franchises and active players to canonical ids.

    Idempotent and conflict-safe (``ProviderIdMap.link``); ambiguous and unresolved players are
    reported, never linked. Historical franchises BALLDONTLIE also lists are skipped and counted.
    """
    with sync_run(db, JOB_IDENTITIES) as run:
        teams = provider.teams()
        if not teams.has_answer:
            raise ProviderSyncError(f"teams: {teams.status.value}: {teams.error}")
        players = provider.active_players()
        if not players.has_answer:
            raise ProviderSyncError(f"players/active: {players.status.value}: {players.error}")
        team_report = crosswalk_teams(db, list(teams.records))
        player_report = crosswalk_players(
            db, list(players.records), season=resolve_roster_season(db)
        )
        run.rows_written = team_report.created + player_report.created
        complete = (
            not team_report.unresolved
            and not team_report.ambiguous
            and not player_report.ambiguous
            and not player_report.unresolved
            and not player_report.conflicts
        )
        run.detail = {
            "provider": PROVIDER,
            "outcome": "complete" if complete else "partial",
            "requests": teams.meta.requests + players.meta.requests,
            "warnings": list(teams.warnings) + list(players.warnings),
            "teams": team_report.summary(),
            "players": player_report.summary(),
            "quarantined_players": player_report.unresolved[:50] + player_report.ambiguous[:50],
            "conflicts": player_report.conflicts[:50],
        }
        for item in player_report.unresolved + player_report.ambiguous:
            upsert_issue(
                db,
                "provider_identity_unresolved",
                f"BALLDONTLIE player {item['provider_id']} ({item['name']}) has no verified "
                "canonical identity; excluded from every BALLDONTLIE-backed table",
                severity="warning",
                entity=f"{PROVIDER}:player:{item['provider_id']}",
            )
        db.commit()
        return run.detail


# ---------------------------------------------------------------------------- contracts


def sync_contracts_from_balldontlie(
    db: Session, provider: BalldontlieProvider, season: Season | str | None = None
) -> dict[str, Any]:
    """One request per franchise (``/contracts/teams``), written into ``contracts``/``contract_years``.

    ``contract_years.salary`` receives the **cap hit** (``salary_basis='cap_hit'``); base salary and
    total cash are stored beside it. Contract headers carry no ``contract_type`` or
    ``no_trade_clause``: BALLDONTLIE's per-year endpoint has neither, and its aggregate endpoint's
    ``contract_type`` vocabulary ("maximum", "Rookie") is not Pivot's (standard/two_way/exhibit_10),
    so nothing is mapped or inferred.
    """
    settings = get_settings()
    target = Season.coerce(season) if season is not None else Season.parse(settings.cap_league_year)
    with sync_run(db, JOB_CONTRACTS) as run:
        ids = ProviderIdMap(db)
        team_links = {
            link.internal_id: link.provider_id
            for link in db.scalars(
                select(ProviderEntityId).where(
                    ProviderEntityId.entity_type == "team", ProviderEntityId.provider == PROVIDER
                )
            )
        }
        if not team_links:
            raise ProviderSyncError(
                "no BALLDONTLIE team links: run the identity sync first (`make refresh-balldontlie "
                "WHAT=identities`)"
            )
        now = datetime.now(UTC)
        records: list[ContractYearRecord] = []
        failed_teams: list[dict[str, str]] = []
        #: Rows the adapter rejected as malformed and rows this job refuses below (R9.3A round 1): a season read with
        #: any of them is ``partial`` and never counts toward the contract horizon.
        read_warnings: list[dict[str, str]] = []
        requests = 0
        for provider_team in sorted(team_links.values(), key=int):
            result = provider.team_contracts(int(provider_team), target)
            requests += result.meta.requests
            if not result.has_answer:
                failed_teams.append(
                    {"team_provider_id": provider_team, "status": result.status.value}
                )
                continue
            for warning in result.warnings:
                read_warnings.append({"team_provider_id": provider_team, "warning": str(warning)[:200]})
            records.extend(result.records)

        # A row is trusted only if it is for the season that was asked for, has one row per player-season, and
        # a cap hit (when present) that is a positive amount. Everything else is refused and listed, never stored.
        refused: list[dict[str, Any]] = []
        seen_keys: dict[tuple[str, str], int] = {}
        for record in records:
            key = (record.player_provider_id, record.season.label)
            seen_keys[key] = seen_keys.get(key, 0) + 1
        accepted: list[ContractYearRecord] = []
        for record in records:
            if record.season != target:
                refused.append({"player": record.player_name, "provider_id": record.player_provider_id,
                                "why": f"season {record.season.label} returned for a {target.label} request"})
            elif seen_keys[(record.player_provider_id, record.season.label)] > 1:
                refused.append({"player": record.player_name, "provider_id": record.player_provider_id,
                                "why": "more than one provider row for this player and season"})
            elif record.cap_hit is not None and record.cap_hit <= 0:
                refused.append({"player": record.player_name, "provider_id": record.player_provider_id,
                                "why": f"non-positive cap hit {record.cap_hit}"})
            else:
                accepted.append(record)
        records = accepted

        identity_extension = link_embedded_players(db, records)
        stats: Counter[str] = Counter(
            dict.fromkeys(
                (
                    "contracts_created",
                    "years_created",
                    "years_updated",
                    "years_unchanged",
                    "years_kept_other_provider",
                ),
                0,
            )
        )
        unmapped: list[dict[str, Any]] = []
        unpriced: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        seen_contract_ids: set[str] = set()
        for record in records:
            player_id = ids.lookup("player", PROVIDER, record.player_provider_id)
            if player_id is None:
                unmapped.append(
                    {"provider_id": record.player_provider_id, "name": record.player_name}
                )
                continue
            if record.cap_hit is None:
                # Never 0, never silently the base salary: the cap hit is unknown.
                unpriced.append(
                    {
                        "player_id": player_id,
                        "name": record.player_name,
                        "base_salary": record.base_salary,
                        "total_cash": record.total_cash,
                    }
                )
                continue
            contract = db.scalar(
                select(Contract).where(Contract.player_id == player_id).order_by(Contract.id).limit(1)
            )
            if contract is None:
                contract = Contract(
                    player_id=player_id,
                    contract_type=None,
                    no_trade_clause=None,
                    source_provider=PROVIDER,
                    source_name=SINGLE_SEASON_CONTRACT_SOURCE,
                    source_date=now.date(),
                    source_retrieved_at=now,
                    source_record_id=record.provider_contract_id,
                    ingestion_run_id=run.id,
                )
                db.add(contract)
                db.flush()
                stats["contracts_created"] += 1
            seen_contract_ids.add(contract.id)
            year = db.scalar(
                select(ContractYear).where(
                    ContractYear.contract_id == contract.id, ContractYear.season == target.label
                )
            )
            if year is not None and year.source_provider != PROVIDER:
                # Another provider already priced this player-season. Leave it; flag a disagreement.
                if year.salary != record.cap_hit:
                    conflicts.append(
                        {
                            "player_id": player_id,
                            "name": record.player_name,
                            "existing_provider": year.source_provider,
                            "existing_salary": year.salary,
                            "balldontlie_cap_hit": record.cap_hit,
                        }
                    )
                    upsert_issue(
                        db,
                        "contract_provider_conflict",
                        f"{record.player_name}: {year.source_provider} has {year.salary}, "
                        f"BALLDONTLIE has cap hit {record.cap_hit} for {target.label}; "
                        "the existing figure was kept",
                        severity="warning",
                        entity=f"{player_id}:{target.label}",
                    )
                stats["years_kept_other_provider"] += 1
                continue
            values = {
                "salary": record.cap_hit,
                "base_salary": record.base_salary,
                "total_cash": record.total_cash,
                "salary_basis": SALARY_BASIS,
                "source_provider": PROVIDER,
                "source_record_id": record.provider_contract_id,
                "source_retrieved_at": now,
                "ingestion_run_id": run.id,
            }
            if year is None:
                db.add(ContractYear(contract_id=contract.id, season=target.label, **values))
                stats["years_created"] += 1
            else:
                figures = {k: values[k] for k in ("salary", "base_salary", "total_cash", "salary_basis")}
                if _apply(year, figures):
                    year.source_retrieved_at = now
                    year.ingestion_run_id = run.id
                    stats["years_updated"] += 1
                else:
                    year.source_retrieved_at = now  # re-observed, unchanged
                    stats["years_unchanged"] += 1
        db.flush()

        # Contracts BALLDONTLIE priced in an earlier run for this season and no longer lists.
        not_reseen = [
            y.contract_id
            for y in db.scalars(
                select(ContractYear).where(
                    ContractYear.season == target.label, ContractYear.source_provider == PROVIDER
                )
            )
            if y.contract_id not in seen_contract_ids
        ]
        roster_season = resolve_roster_season(db)
        coverage = contract_coverage(db, roster_season, target.label)
        uncovered = roster_side_unmatched(db, roster_season, target.label)
        # Complete means: every team answered, no row was rejected or refused, and the season is not empty. A
        # season that fails any of these is `partial` and the contract horizon will not count it.
        clean_read = not failed_teams and not read_warnings and not refused and bool(records)
        complete = clean_read and not unmapped
        run.rows_written = stats["years_created"] + stats["years_updated"]
        run.detail = {
            "provider": PROVIDER,
            "season": target.label,
            "outcome": "complete" if clean_read else "partial",
            "salary_basis": SALARY_BASIS,
            "requests": requests,
            "teams_requested": len(team_links),
            "teams_failed": failed_teams,
            "read_warnings": read_warnings[:20],
            "refused_rows": refused[:50],
            "refused_rows_total": len(refused),
            "identity_extension": identity_extension,
            "rows_returned": len(records),
            **dict(stats),
            "not_reseen": len(not_reseen),
            "unmapped_players": unmapped[:50],
            "unmapped_players_total": len(unmapped),
            "unpriced_players": unpriced[:300],
            "unpriced_players_total": len(unpriced),
            "provider_conflicts": conflicts[:50],
            "provider_conflicts_total": len(conflicts),
            "coverage": coverage,
            "roster_players_without_salary": uncovered,
            "summary": summarize(coverage, uncovered),
            "complete_read": complete,
        }
        logger.info("sync_contracts[balldontlie]: %s", run.detail["summary"])
        db.commit()
        return run.detail


# ----------------------------------------------------------------------------- injuries


def sync_injuries_from_balldontlie(db: Session, provider: BalldontlieProvider) -> dict[str, Any]:
    """The league-wide injury list as the provider states it right now.

    A player in the feed gets one *open* ``injuries`` row (``valid_to`` NULL) that is updated in
    place. A player who was open and is no longer listed is closed with ``valid_to`` — the provider
    stopped reporting an injury, which is not a statement that the player is available.
    """
    with sync_run(db, JOB_INJURIES) as run:
        result = provider.injuries(max_pages=20)
        if not result.has_answer:
            raise ProviderSyncError(f"player_injuries: {result.status.value}: {result.error}")
        if result.warnings and any("TRUNCATED" in w for w in result.warnings):
            raise ProviderSyncError(
                "injury list was truncated; refusing to close injuries on an incomplete read"
            )
        ids = ProviderIdMap(db)
        now = datetime.now(UTC)
        stats: Counter[str] = Counter(dict.fromkeys(("opened", "updated", "unchanged", "closed"), 0))
        unmapped: list[str] = []
        listed: set[str] = set()
        for record in result.records:
            player_id = ids.lookup("player", PROVIDER, record.player_provider_id)
            if player_id is None:
                unmapped.append(record.player_provider_id)
                continue
            listed.add(player_id)
            return_date = _parse_date(record.return_date)
            open_row = db.scalar(
                select(Injury).where(
                    Injury.player_id == player_id,
                    Injury.source_provider == PROVIDER,
                    Injury.valid_to.is_(None),
                )
            )
            values = {
                "status": record.status[:30],
                "description": record.description,
                "return_date": return_date,
            }
            if open_row is None:
                db.add(
                    Injury(
                        player_id=player_id,
                        source_provider=PROVIDER,
                        source_retrieved_at=now,
                        valid_from=now,
                        ingestion_run_id=run.id,
                        **values,
                    )
                )
                stats["opened"] += 1
            elif _apply(open_row, values):
                open_row.source_retrieved_at = now
                stats["updated"] += 1
            else:
                open_row.source_retrieved_at = now
                stats["unchanged"] += 1
        for stale in db.scalars(
            select(Injury).where(Injury.source_provider == PROVIDER, Injury.valid_to.is_(None))
        ):
            if stale.player_id not in listed:
                stale.valid_to = now
                stats["closed"] += 1
        run.rows_written = stats["opened"] + stats["updated"]
        run.detail = {
            "provider": PROVIDER,
            "outcome": "complete",
            "requests": result.meta.requests,
            "rows_returned": len(result.records),
            **dict(stats),
            "unmapped_players_total": len(unmapped),
            "note": "valid_to means the provider stopped listing the injury, not that the player is healthy",
        }
        db.commit()
        return run.detail


def _parse_date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw[:10]) if raw else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------- statistics


def sync_stats_from_balldontlie(
    db: Session,
    provider: BalldontlieProvider,
    season: Season | str,
    season_type: SeasonType = SeasonType.REGULAR,
    groups: Iterable[str] = DEFAULT_STAT_GROUPS,
    *,
    teams: bool = True,
) -> dict[str, Any]:
    """Persist complete provider statistic groups as ``provider_stat_snapshots``.

    One scope = (season, season_type); regular and playoff rows never share a key. A group that was
    read incompletely (capped pagination, failed request) is **not** persisted at all — a partial
    league table filed as "2025-26 general/advanced" would read as complete. Provider-relative
    ``*_rank`` fields are dropped; missing figures stay null.
    """
    scope = StatScope(Season.coerce(season), season_type)
    unknown = [g for g in groups if g not in STAT_GROUPS]
    if unknown:
        raise ValueError(f"unknown statistic group(s) {unknown}; known: {sorted(STAT_GROUPS)}")
    with sync_run(db, JOB_STATS) as run:
        ids = ProviderIdMap(db)
        now = datetime.now(UTC)
        report: dict[str, Any] = {}
        total_written = 0
        requests = 0
        for group in groups:
            category, stat_type, aggregation = STAT_GROUPS[group]
            outcome: dict[str, Any] = {"aggregation": aggregation}
            report[group] = outcome
            for entity in ("player", "team") if teams else ("player",):
                key = entity
                try:
                    result = (
                        provider.player_season_stats(
                            scope.season, season_type, category=category, stat_type=stat_type
                        )
                        if entity == "player"
                        else provider.team_season_stats(
                            scope.season, season_type, category=category, stat_type=stat_type
                        )
                    )
                except InvalidStatPairingError as exc:
                    outcome[key] = {"persisted": False, "reason": f"not published: {exc}"}
                    continue
                requests += result.meta.requests
                if not result.has_answer:
                    outcome[key] = {"persisted": False, "status": result.status.value}
                    continue
                if any("TRUNCATED" in w or "repeated" in w for w in result.warnings):
                    outcome[key] = {"persisted": False, "reason": "incomplete read"}
                    continue
                if not result.records:
                    outcome[key] = {"persisted": False, "reason": "provider returned no rows"}
                    continue
                counts: Counter[str] = Counter()
                if entity == "player":
                    extension = link_embedded_players(db, result.records)
                    if extension.get("attempted"):
                        outcome["identity_extension"] = extension
                for rec in result.records:
                    provider_id = (
                        rec.player_provider_id
                        if isinstance(rec, PlayerStatRecord)
                        else str(rec.team_provider_id)
                    )
                    internal = ids.lookup(entity, PROVIDER, provider_id)
                    if internal is None:
                        counts["unmapped"] += 1
                        continue
                    values = {k: v for k, v in rec.values.items() if not k.endswith("_rank")}
                    games = getattr(rec, "games_played", None)
                    row = db.scalar(
                        select(ProviderStatSnapshot).where(
                            ProviderStatSnapshot.provider == PROVIDER,
                            ProviderStatSnapshot.entity_type == entity,
                            ProviderStatSnapshot.internal_id == internal,
                            ProviderStatSnapshot.season == scope.season.label,
                            ProviderStatSnapshot.season_type == season_type.value,
                            ProviderStatSnapshot.stat_group == group,
                        )
                    )
                    fields = {
                        "aggregation": aggregation,
                        "basis": rec.basis,
                        "games_played": games,
                        "values": values,
                        "source_record_id": provider_id,
                    }
                    if row is None:
                        db.add(
                            ProviderStatSnapshot(
                                provider=PROVIDER,
                                entity_type=entity,
                                internal_id=internal,
                                season=scope.season.label,
                                season_type=season_type.value,
                                stat_group=group,
                                retrieved_at=now,
                                ingestion_run_id=run.id,
                                **fields,
                            )
                        )
                        counts["inserted"] += 1
                    elif _apply(row, fields):
                        row.retrieved_at = now
                        row.ingestion_run_id = run.id
                        counts["updated"] += 1
                    else:
                        row.retrieved_at = now
                        counts["unchanged"] += 1
                total_written += counts["inserted"] + counts["updated"]
                outcome[key] = {"persisted": True, "returned": len(result.records), **dict(counts)}
        db.flush()
        persisted = [
            g for g, o in report.items() if any(isinstance(v, dict) and v.get("persisted") for v in o.values())
        ]
        run.rows_written = total_written
        run.detail = {
            "provider": PROVIDER,
            "season": scope.season.label,
            "season_type": season_type.value,
            "outcome": "complete" if len(persisted) == len(list(groups)) else "partial",
            "requests": requests,
            "groups": report,
        }
        db.commit()
        return run.detail


# ----------------------------------------------------------------------------- read side


def contract_link_summary(db: Session) -> dict[str, int]:
    """How many players have a verified BALLDONTLIE link (a quick health number)."""
    return {
        "player_links": len(
            db.scalars(
                select(ProviderEntityId.id).where(
                    ProviderEntityId.provider == PROVIDER, ProviderEntityId.entity_type == "player"
                )
            ).all()
        ),
        "players": len(db.scalars(select(Player.id)).all()),
    }


def default_cache_dir() -> Path:
    from app.config import BACKEND_DIR

    return BACKEND_DIR.parent / "data" / "imports" / "balldontlie" / "cache"
