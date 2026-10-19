"""R9.3A — an injury reading is dated by its retrieval, stale is never "today", and absence is never "healthy"."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import DataSyncRun, Injury
from app.services.injury_status import FRESH_HOURS, injury_status
from tests.conftest import make_player, make_team

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _injury(db: Session, player, hours_ago: float, *, status="Out", return_date=date(2026, 11, 1), closed=False):
    retrieved = NOW - timedelta(hours=hours_ago)
    db.add(
        Injury(
            player_id=player.id, status=status, return_date=return_date, source_provider="balldontlie",
            source_retrieved_at=retrieved, valid_to=retrieved if closed else None,
        )
    )
    db.add(DataSyncRun(job_name="sync_injuries", status="succeeded", finished_at=retrieved, detail={}))
    db.commit()


def test_a_fresh_reading_is_current_as_of_its_retrieval(db: Session) -> None:
    p = make_player(db, 1, "Hurt", make_team(db, 1, "AAA"))
    _injury(db, p, hours_ago=3)
    out = injury_status(db, p.id, now=NOW)
    assert out["available"] and out["freshness"] == "fresh" and out["framing"] == "current as of retrieval"
    assert out["has_report_date"] is False and out["caveats"] == []


def test_an_old_reading_is_last_reported_never_today(db: Session) -> None:
    p = make_player(db, 1, "Hurt", make_team(db, 1, "AAA"))
    _injury(db, p, hours_ago=FRESH_HOURS + 30)
    out = injury_status(db, p.id, now=NOW)
    assert out["freshness"] == "stale" and "may no longer be current" in out["framing"]
    assert any("hours ago" in c for c in out["caveats"])


def test_a_passed_return_date_is_flagged(db: Session) -> None:
    p = make_player(db, 1, "Hurt", make_team(db, 1, "AAA"))
    _injury(db, p, hours_ago=2, return_date=date(2026, 10, 1))
    out = injury_status(db, p.id, now=NOW)
    assert out["return_date_passed"] is True and any("return date has passed" in c for c in out["caveats"])


def test_no_row_is_none_on_file_not_healthy(db: Session) -> None:
    p = make_player(db, 1, "Fine", make_team(db, 1, "AAA"))
    out = injury_status(db, p.id, now=NOW)
    assert out["available"] is False and "not the same as healthy" in out["note"]
    assert "healthy" not in out["reason"].lower()


def test_a_closed_row_says_no_longer_reported_not_healthy(db: Session) -> None:
    p = make_player(db, 1, "Back", make_team(db, 1, "AAA"))
    _injury(db, p, hours_ago=5, closed=True)
    out = injury_status(db, p.id, now=NOW)
    assert out["available"] is False and "not a statement that he is healthy" in out["reason"]


def test_an_unknown_player(db: Session) -> None:
    assert injury_status(db, "nope")["available"] is False
