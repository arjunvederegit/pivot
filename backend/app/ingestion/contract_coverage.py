"""What a contract snapshot actually covers, measured from the **roster** side.

The metric that matters is not the one the import naturally reports. A BBRef-side match
rate answers "how many rows in the file did we recognise", and can read 98 % while the
question the product needs answered — "can we compute payroll for this team" — is 60 %.

Payroll used to be all-or-nothing, which made coverage a cliff rather than a slope.
Measured sensitivity on the live 530-row roster:

    roster players missing a 2026-27 salary → teams with a payroll
     1 %  → 26/30      2 %  → 21/30      5 %  → 10/30
    10 %  →  4/30     20 %  →  0/30

An offseason snapshot plausibly misses 25–40 % (expired contracts) → **zero teams with
payroll**, while a BBRef-side match rate reports success the whole time.

R2c split that single number in two, and both are reported here because they answer
different questions:

- `teams_with_complete_payroll` — teams whose payroll may be **compared to a threshold**.
  Still all-or-nothing, still the gate every salary rule reads. Measured 0/30 against the
  Basketball-Reference offseason snapshot.
- `teams_with_disclosable_payroll` — teams for which a payroll figure can be **shown**
  with its coverage attached, as a lower bound. Measured 30/30 against the same snapshot.

An operator judging an import needs both: the second says the data is worth loading, the
first says whether legality can be verified from it. This module runs whether or not a
provider is configured.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.rosters import current_roster_clause
from app.cba import resolver
from app.db.models import Contract, ContractYear, RosterEntry, Team


def contract_coverage(db: Session, season: str, league_year: str) -> dict:
    """Roster-side coverage for `league_year`, plus the shape of what is on file."""
    entries = list(
        db.scalars(
            select(RosterEntry).where(current_roster_clause(season))
        ).all()
    )
    teams = {t.id: t.abbreviation for t in db.scalars(select(Team)).all()}
    by_team: dict[str, list[RosterEntry]] = {}
    for entry in entries:
        by_team.setdefault(entry.team_id, []).append(entry)

    resolved = resolver.salaries(db, [e.player_id for e in entries], league_year)
    with_salary = {pid for pid, (salary, _) in resolved.items() if salary is not None}

    teams_complete = 0
    teams_disclosable = 0
    team_shares: list[float] = []
    incomplete: list[dict] = []
    for team_id, roster in sorted(by_team.items(), key=lambda kv: teams.get(kv[0], kv[0])):
        missing = [e.player_id for e in roster if e.player_id not in with_salary]
        covered_here = len(roster) - len(missing)
        if covered_here:
            # R2c: at least one priced contract means a lower-bound payroll can be shown
            # with its coverage. It does not mean the payroll can be verified.
            teams_disclosable += 1
        if roster:
            team_shares.append(covered_here / len(roster))
        if not missing:
            teams_complete += 1
        else:
            incomplete.append(
                {
                    "team": teams.get(team_id, team_id),
                    "roster_size": len(roster),
                    "missing": len(missing),
                    "covered": covered_here,
                }
            )

    # Which league years the snapshot actually carries. An import that has every player
    # but only for 2025-26 is useless for a 2026-27 cap check, and would otherwise look
    # like a complete success.
    seasons_present = sorted(
        {
            row
            for (row,) in db.execute(select(ContractYear.season).distinct()).all()
            if row
        }
    )
    contracts_on_file = db.scalar(select(Contract).limit(1)) is not None

    total = len(entries)
    covered = len([e for e in entries if e.player_id in with_salary])
    return {
        "season": season,
        "cap_league_year": league_year,
        "roster_players_total": total,
        "roster_players_with_salary_for_cap_league_year": covered,
        "roster_coverage_share": round(covered / total, 4) if total else None,
        "teams_total": len(by_team),
        # Payroll may be compared to a cap threshold — the gate every salary rule reads.
        "teams_with_complete_payroll": teams_complete,
        # Payroll may be shown as a lower bound with its coverage attached (R2c).
        "teams_with_disclosable_payroll": teams_disclosable,
        "team_coverage_share_min": round(min(team_shares), 4) if team_shares else None,
        "team_coverage_share_median": (
            round(sorted(team_shares)[len(team_shares) // 2], 4) if team_shares else None
        ),
        "teams_incomplete": incomplete,
        "seasons_present_in_snapshot": seasons_present,
        "cap_league_year_present_in_snapshot": league_year in seasons_present,
        "contracts_on_file": contracts_on_file,
    }


def roster_side_unmatched(
    db: Session, season: str, league_year: str, limit: int = 100
) -> list[dict]:
    """Rostered players with no salary for `league_year` — the list that matters.

    The import's own `unmatched_players` is the *provider's* side: names in the file that
    no player matched. It says nothing about a rostered player the file never mentioned,
    which is exactly the case that makes payroll unavailable.
    """
    entries = list(
        db.scalars(
            select(RosterEntry).where(current_roster_clause(season))
        ).all()
    )
    resolved = resolver.salaries(db, [e.player_id for e in entries], league_year)
    teams = {t.id: t.abbreviation for t in db.scalars(select(Team)).all()}
    players = resolver.players(db, [e.player_id for e in entries])
    rows = [
        {
            "team": teams.get(entry.team_id, entry.team_id),
            "player": players[entry.player_id].full_name
            if entry.player_id in players
            else entry.player_id,
            "reason": "no contract on file"
            if resolved[entry.player_id][1] is None
            else f"contract on file but no {league_year} year",
        }
        for entry in entries
        if resolved[entry.player_id][0] is None
    ]
    rows.sort(key=lambda r: (r["team"], r["player"]))
    return rows[:limit]


def summarize(coverage: dict, unmatched: Iterable[dict]) -> str:
    """One line an operator can act on."""
    if not coverage["contracts_on_file"]:
        return (
            "No contracts on file. Salary matching, payroll and apron status stay "
            "unavailable product-wide."
        )
    if not coverage["cap_league_year_present_in_snapshot"]:
        return (
            f"The snapshot carries {coverage['seasons_present_in_snapshot']} but not "
            f"{coverage['cap_league_year']}, which is the league year trade legality is "
            "evaluated under. No team will have a payroll."
        )
    share = coverage["roster_coverage_share"] or 0
    return (
        f"{coverage['roster_players_with_salary_for_cap_league_year']}/"
        f"{coverage['roster_players_total']} rostered players ({share:.1%}) have a "
        f"{coverage['cap_league_year']} salary; "
        f"{coverage['teams_with_disclosable_payroll']}/{coverage['teams_total']} teams can "
        "show a payroll with disclosed coverage, "
        f"{coverage['teams_with_complete_payroll']}/{coverage['teams_total']} can have one "
        f"verified against a cap threshold. {len(list(unmatched))} rostered players are "
        "listed as uncovered."
    )
