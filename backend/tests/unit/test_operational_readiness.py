"""R9.3A — the readiness inventory never claims more than the data holds, and classes what it finds."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import Injury
from app.services.operational_readiness import operational_readiness
from tests.conftest import make_player, make_team

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def test_an_empty_database_reports_zeroes_and_unavailable_not_errors(db: Session) -> None:
    out = operational_readiness(db, now=NOW)
    assert out["rosters"]["players"] == 0 and out["contracts"]["rostered_coverage"] is None
    assert out["contracts"]["teams_with_complete_payroll"] == 0
    assert out["player_intelligence"]["needs_materialise_skills"] is True
    assert set(out["classes"]) == {"verified_display", "experimental_preview", "partial", "unavailable"}


def test_contracts_are_classed_partial_and_never_complete_while_anyone_is_unpriced(db: Session, cap_params) -> None:
    team = make_team(db, 1, "AAA")
    make_player(db, 1, "Priced", team, salary=5_000_000, contract_type=None)
    make_player(db, 2, "Unpriced", team, salary=None)
    out = operational_readiness(db, now=NOW)
    c = out["contracts"]
    assert c["class"] == "partial"
    assert c["rostered_players_priced"] == 1 and c["rostered_players"] == 2
    assert c["teams_with_complete_payroll"] == 0
    assert c["unpriced_reasons"] == {"no contract row": 1}


def test_injuries_report_their_age_and_the_missing_report_date(db: Session) -> None:
    p = make_player(db, 1, "Hurt", make_team(db, 1, "AAA"))
    db.add(Injury(player_id=p.id, status="Out", source_provider="balldontlie", source_retrieved_at=NOW))
    db.commit()
    out = operational_readiness(db, now=NOW)
    assert out["injuries"]["rows"] == 1 and out["injuries"]["with_report_date"] == 0
    assert out["injuries"]["class"] == "partial"
