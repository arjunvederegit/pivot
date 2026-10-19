"""R9.3A — a read-only inventory of what the application can actually serve today, by evidence class.

The question is not "is the column populated?" but "may a tool show this, and how?". Every dataset lands in one of
four classes (the same four the R9.3A reports use):

* ``verified_display``       — safe to show as a fact, with its source, season and freshness;
* ``experimental_preview``   — shown only labelled as experimental (candidate model output);
* ``partial``                — usable, but coverage is incomplete and the gap must be shown with it;
* ``unavailable``            — not in the data, or in it but not validated for any claim.

Nothing here writes, fetches or trains. ``operational_readiness(db)`` returns plain JSON-serialisable data, so the
CLI (``make r93a-readiness``) and the documentation are generated from the same numbers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.canonical.rosters import current_roster_clause, resolve_roster_season
from app.canonical.tool_seasons import tool_seasons
from app.config import get_settings
from app.db.models import (
    CandidateEstimate,
    Contract,
    ContractYear,
    Injury,
    LeagueCapParameters,
    ModelVersion,
    PlayerImpactEstimate,
    PlayerSeasonStats,
    ProviderEntityId,
    ProviderStatSnapshot,
    RosterEntry,
    Team,
)
from app.modeling.contract import OutputKind
from app.player_intelligence.quantities import QUANTITIES
from app.services.financials import horizon, team_financials


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _age_hours(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return round((now - aware).total_seconds() / 3600, 1)


def operational_readiness(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    settings = get_settings()
    financial_season = settings.cap_league_year
    roster_season = resolve_roster_season(db)
    seasons = tool_seasons(db, roster_season=roster_season)

    # ------------------------------------------------------------------ rosters
    entries = list(db.scalars(select(RosterEntry).where(current_roster_clause(roster_season))).all())
    teams = {t.id: t.abbreviation for t in db.scalars(select(Team)).all()}
    by_team = Counter(e.team_id for e in entries)
    roster_ids = [e.player_id for e in entries]
    links = {
        row.internal_id: row
        for row in db.scalars(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == "player", ProviderEntityId.provider == "balldontlie"
            )
        ).all()
    }
    link_confidence = Counter(links[p].confidence for p in roster_ids if p in links)
    rosters = {
        "season": roster_season,
        "provisional": bool((seasons.get("roster") or {}).get("provisional")),
        "status": (seasons.get("roster") or {}).get("status"),
        "teams": len(by_team),
        "players": len(entries),
        "team_size": {"min": min(by_team.values(), default=0), "max": max(by_team.values(), default=0)},
        "last_snapshot": (seasons.get("roster") or {}).get("snapshot_retrieved_at"),
        "balldontlie_identity_links": {
            "linked": sum(1 for p in roster_ids if p in links),
            "of": len(roster_ids),
            "by_confidence": dict(link_confidence),
            "unlinked": sorted(
                e.player.full_name for e in entries if e.player_id not in links
            ),
        },
        "class": "partial" if (seasons.get("roster") or {}).get("provisional") else "verified_display",
        "class_reason": "NBA.com roster listings in the preseason cannot distinguish camp, Exhibit 10 and two-way players",
    }

    # ---------------------------------------------------------------- statistics
    stat_seasons = {
        str(season): int(n)
        for season, n in (
            db.execute(
            select(PlayerSeasonStats.season, func.count()).where(PlayerSeasonStats.stat_type == "base").group_by(PlayerSeasonStats.season)
            ).all()
        )
    }
    snapshot_groups = [
        {"stat_group": g, "season": s, "season_type": st, "players": n, "aggregation": a, "retrieved_at": _iso(r)}
        for g, s, st, a, n, r in db.execute(
            select(
                ProviderStatSnapshot.stat_group,
                ProviderStatSnapshot.season,
                ProviderStatSnapshot.season_type,
                ProviderStatSnapshot.aggregation,
                func.count(),
                func.max(ProviderStatSnapshot.retrieved_at),
            )
            .where(ProviderStatSnapshot.provider == "balldontlie", ProviderStatSnapshot.entity_type == "player")
            .group_by(
                ProviderStatSnapshot.stat_group,
                ProviderStatSnapshot.season,
                ProviderStatSnapshot.season_type,
                ProviderStatSnapshot.aggregation,
            )
            .order_by(ProviderStatSnapshot.stat_group)
        ).all()
    ]
    statistics = {
        "performance_season": (seasons.get("performance") or {}).get("season"),
        "performance_state": (seasons.get("performance") or {}).get("state"),
        "league_season_regular_stats": (seasons.get("performance") or {}).get("league_season_regular_stats"),
        "nba_com_player_seasons_base": dict(sorted(stat_seasons.items())),
        "balldontlie_snapshots": snapshot_groups,
        "class": "verified_display",
        "class_reason": "NBA.com season statistics; BALLDONTLIE general/base and general/advanced are numerically identical to them (R8.3). "
        "The other families are stored as supplied: shot zones are unit-reconciled per player at read time (see shot_zones.py).",
    }

    # ----------------------------------------------------------------- contracts
    info = horizon(db, financial_season)
    team_rows = []
    priced_total = 0
    unpriced_reasons: Counter[str] = Counter()
    complete_teams = 0
    for team_id, abbr in sorted(teams.items(), key=lambda kv: kv[1]):
        if by_team.get(team_id, 0) == 0:
            continue
        fin = team_financials(db, team_id, financial_season, roster_season)
        priced_total += len(fin.priced)
        for p in fin.unpriced:
            kind = "provider row without a cap hit" if p.status == "unpriced" else "no contract row"
            if "two-way salary" in p.status_reason:
                kind += " (cash equals the two-way salary)"
            unpriced_reasons[kind] += 1
        complete_teams += int(fin.complete)
        team_rows.append(
            {
                "team": abbr,
                "roster": fin.roster_size,
                "priced": len(fin.priced),
                "known_subtotal": fin.known_subtotal,
                "complete": fin.complete,
            }
        )
    rows_by_season = {
        str(season): int(n)
        for season, n in db.execute(select(ContractYear.season, func.count()).group_by(ContractYear.season)).all()
    }
    contracts = {
        "financial_season": financial_season,
        "contract_rows": db.scalar(select(func.count()).select_from(Contract)) or 0,
        "contract_years_by_season": dict(sorted(rows_by_season.items())),
        "horizon": info.as_dict(),
        "rostered_players_priced": priced_total,
        "rostered_players": len(entries),
        "rostered_coverage": round(priced_total / len(entries), 4) if entries else None,
        "teams_with_complete_payroll": complete_teams,
        "unpriced_reasons": dict(unpriced_reasons),
        "teams": team_rows,
        "salary_bases": sorted(
            {b for (b,) in db.execute(select(ContractYear.salary_basis).distinct()).all() if b}
        ),
        "not_loaded": ["option flags", "guaranteed amounts", "no-trade clauses", "signing dates", "contract type", "cap holds", "dead money"],
        "class": "partial",
        "class_reason": "cap hits are verified as supplied by the provider for most rostered players; no team's payroll is complete, "
        "so payroll, cap space and apron position are withheld",
    }

    # ----------------------------------------------------------------- injuries
    injury_rows = list(db.scalars(select(Injury)).all())
    latest = max((i.source_retrieved_at for i in injury_rows if i.source_retrieved_at), default=None)
    injuries = {
        "rows": len(injury_rows),
        "by_status": dict(Counter(i.status for i in injury_rows)),
        "with_return_date": sum(1 for i in injury_rows if i.return_date),
        "with_report_date": sum(1 for i in injury_rows if i.reported_at),
        "closed": sum(1 for i in injury_rows if i.valid_to),
        "last_retrieved": _iso(latest),
        "age_hours": _age_hours(latest, moment),
        "class": "partial",
        "class_reason": "the provider supplies no report date, so the only date is Pivot's own retrieval time; a status older than a day "
        "is shown as 'as of <retrieval>', never as today's status",
    }

    # ------------------------------------------------------------------ R9.2
    candidates = defaultdict(list)
    for mv in db.scalars(select(ModelVersion).where(ModelVersion.model_name.like("player_forecast_%"))).all():
        n = db.scalar(select(func.count()).select_from(CandidateEstimate).where(CandidateEstimate.model_version_id == mv.id)) or 0
        candidates[mv.model_name].append(
            {"version": mv.version, "status": mv.status, "is_active": mv.is_active, "estimates": n}
        )
    tei_model = db.scalar(select(ModelVersion).where(ModelVersion.model_name == "player_impact", ModelVersion.is_active))
    tei_estimates = (
        db.scalar(select(func.count()).select_from(PlayerImpactEstimate).where(PlayerImpactEstimate.model_version_id == tei_model.id))
        if tei_model
        else 0
    )
    player_intelligence = {
        "preview_route_enabled": settings.player_intelligence_preview,
        "measured_quantities_served": sum(1 for q in QUANTITIES.values() if OutputKind.MEASURED in q.kinds),
        "candidate_models": dict(candidates),
        "needs_materialise_skills": not candidates,
        "active_tei_model": tei_model.version if tei_model else None,
        "active_tei_estimates": tei_estimates,
        "class": "experimental_preview" if candidates else "verified_display",
        "any_candidate_active": any(v["is_active"] or v["status"] == "active" for vs in candidates.values() for v in vs),
        "class_reason": "measured values are served from stored season rows; inferred and projected values come only from stored candidate "
        "models and are labelled experimental unless a record's own model fields say active and holdout-confirmed",
    }

    cap_years = {y for (y,) in db.execute(select(LeagueCapParameters.league_year)).all()}
    return {
        "as_of": moment.isoformat(),
        "financial_season": financial_season,
        "roster_season": roster_season,
        "statistics_season": statistics["performance_season"],
        "rosters": rosters,
        "statistics": statistics,
        "contracts": contracts,
        "injuries": injuries,
        "player_intelligence": player_intelligence,
        "classes": {
            "verified_display": [f"NBA.com season statistics ({statistics['performance_season']} regular season)", "measured player quantities", "cap parameters on file: " + (", ".join(sorted(cap_years)) or "none")],
            "experimental_preview": ["inferred shooting ability", "next-season shooting projections (conditional on playing)"],
            "partial": [f"{roster_season} rosters" + (" (provisional)" if rosters["provisional"] else ""), "contracts (cap hits for most rostered players, no complete payroll)", "injury status (dated by retrieval only)"],
            "unavailable": ["team payroll / cap space / apron position", "contract options, guarantees, no-trade clauses, signing dates", "defence, shot-location impact, overall rating"],
        },
    }
