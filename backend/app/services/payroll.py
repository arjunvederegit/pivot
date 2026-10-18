"""Team payroll summaries under the R2c disclosed-coverage model.

Before R2c this returned `payroll: None` whenever a single rostered player was unpriced,
which was right about verdicts and wrong about disclosure — measured against the
Basketball-Reference offseason snapshot it made **0 of 30** teams report a payroll while
74 % of rostered players had a salary on file.

The summary now carries three separate things, and they are never merged:

- `payroll_known` — the sum of the contracts actually on file. A **lower bound**: every
  missing salary is a non-negative amount, so the real payroll is this number plus the
  unpriced remainder. Nothing is imputed.
- `payroll_coverage` — how many of how many, and what is missing, so the number is never
  rendered bare.
- `payroll` / `payroll_available` — unchanged. Still `None` unless every rostered player
  is priced. This is the only figure a caller may compare against a cap threshold, and
  it is what keeps an incomplete roster from producing a verified cap position.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.rosters import current_roster_clause, resolve_roster_season
from app.cba.builder import load_cap_params
from app.cba.context import CapParams, PayrollCoverage
from app.cba.resolver import salaries
from app.config import get_settings
from app.core.errors import DataUnavailableError
from app.db.models import RosterEntry, Team
from app.integrations.contracts import get_contract_provider


def team_payroll_summary(db: Session, team: Team) -> dict:
    settings = get_settings()
    league_year = settings.cap_league_year
    season = resolve_roster_season(db)
    try:
        params = load_cap_params(db, league_year)
    except DataUnavailableError:
        params = None

    entries = db.scalars(
        select(RosterEntry).where(RosterEntry.team_id == team.id, current_roster_clause(season))
    ).all()

    resolved = salaries(db, [entry.player_id for entry in entries], league_year)
    players = []
    total = 0
    known = 0
    missing: list[str] = []
    for entry in entries:
        salary, contract = resolved[entry.player_id]
        if salary is not None:
            total += salary
            known += 1
        else:
            missing.append(entry.player.full_name)
        players.append(
            {
                "player_id": entry.player_id,
                "name": entry.player.full_name,
                "salary": salary,
                "contract_type": contract.contract_type if contract else None,
                "source_name": contract.source_name if contract else None,
                "source_date": contract.source_date.isoformat()
                if contract and contract.source_date
                else None,
            }
        )

    coverage = PayrollCoverage(known=total, players_known=known, players_total=len(entries))
    provider_configured = get_contract_provider() is not None
    summary: dict = {
        "league_year": league_year,
        # R8.2: the roster the payroll is summed over, and the season the salaries are for. They
        # are looked up for `league_year` only; no other season's salary stands in for it.
        "roster_season": season,
        "roster_size": coverage.players_total,
        "players_with_salary": coverage.players_known,
        "players_without_salary": coverage.players_unknown,
        # Verified payroll — complete coverage only. Unchanged by R2c.
        "payroll": coverage.verified,
        "payroll_available": coverage.complete,
        # Disclosed payroll — a lower bound, always accompanied by its coverage.
        "payroll_known": coverage.known if coverage.players_total else None,
        "payroll_is_lower_bound": not coverage.complete,
        "payroll_coverage": coverage.as_dict(),
        "payroll_coverage_note": coverage.disclosure(),
        "players_missing_salary": sorted(missing),
        "players": players,
        "contract_provider_configured": provider_configured,
    }
    if not coverage.complete:
        summary["unavailable_reason"] = _unavailable_reason(coverage, provider_configured)

    if params:
        # Cap context accompanies the *verified* payroll only. With partial coverage the
        # one sound statement is the floor the known salaries already clear — a lower
        # bound can prove a team is over a line, never that it is under one.
        if coverage.verified is not None:
            summary["cap_context"] = {
                "salary_cap": params.salary_cap,
                "luxury_tax": params.luxury_tax,
                "first_apron": params.first_apron,
                "second_apron": params.second_apron,
                "room_below_tax": params.luxury_tax - coverage.verified,
                "cap_source": params.source_name,
            }
        elif coverage.players_known:
            summary["cap_context_partial"] = {
                "salary_cap": params.salary_cap,
                "luxury_tax": params.luxury_tax,
                "first_apron": params.first_apron,
                "second_apron": params.second_apron,
                "cap_source": params.source_name,
                "thresholds_already_cleared": _cleared(coverage.known, params),
                "note": (
                    "Room below the tax is not shown: it would require the unpriced "
                    f"{coverage.players_unknown} salaries. Only thresholds the known "
                    "contracts already exceed can be stated from partial data."
                ),
            }
    return summary


def _cleared(known: int, params: CapParams) -> list[str]:
    """Thresholds the known salaries alone already exceed — sound under any completion."""
    thresholds = [
        ("salary_cap", params.salary_cap),
        ("luxury_tax", params.luxury_tax),
        ("first_apron", params.first_apron),
        ("second_apron", params.second_apron),
    ]
    return [name for name, value in thresholds if known > value]


def _unavailable_reason(coverage: PayrollCoverage, provider_configured: bool) -> str:
    if coverage.players_total == 0:
        return "No current roster on file for this team."
    if coverage.players_known == 0:
        return (
            "Contract data unavailable from the configured provider. Payroll, tax and "
            "apron status cannot be computed."
            if provider_configured
            else "No contract provider is configured (nba_api does not supply contracts). "
            "See data/contracts/README.md to enable one."
        )
    return (
        f"{coverage.players_unknown} of {coverage.players_total} rostered players have no "
        f"{'salary on file' if provider_configured else 'contract provider configured'}. "
        "The committed salary shown is a lower bound built from the contracts that are on "
        "file; tax and apron position stay unavailable because the missing salaries could "
        "cross any threshold."
    )
