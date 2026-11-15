"""Migration round-trip and data-quality checks."""

from datetime import UTC, datetime

import pytest
from alembic.config import Config

from alembic import command
from app.config import BACKEND_DIR, get_settings
from app.db.models import (
    Contract,
    ContractYear,
    DataQualityIssue,
    LeagueCapParameters,
    PlayerSeasonStats,
    Standing,
)
from app.ingestion.quality import validate_data
from tests.conftest import make_player, make_team


def test_alembic_upgrade_head_on_fresh_database(tmp_sqlite_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", tmp_sqlite_url)
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(config, "head")

        from sqlalchemy import create_engine, inspect

        inspector = inspect(create_engine(tmp_sqlite_url))
        tables = set(inspector.get_table_names())
        for expected in (
            "teams",
            "players",
            "rosters",
            "trade_proposals",
            "league_cap_parameters",
            "data_sync_runs",
        ):
            assert expected in tables
    finally:
        get_settings.cache_clear()


@pytest.fixture()
def seasons(monkeypatch, request):
    """Pin the two clocks the quality checks read: ``seasons(stats="2025-26", cap="2026-27")``.

    ``validate_data`` calls ``get_settings()`` on every run, so the cached object is cleared
    whenever the environment moves and again at teardown.
    """

    def _set(stats: str, cap: str) -> None:
        monkeypatch.setenv("CURRENT_SEASON", stats)
        monkeypatch.setenv("CAP_LEAGUE_YEAR", cap)
        get_settings.cache_clear()

    request.addfinalizer(get_settings.cache_clear)
    return _set


def _add_cap_year(db, league_year: str) -> None:
    db.add(
        LeagueCapParameters(
            league_year=league_year,
            salary_cap=164_961_000,
            luxury_tax=200_428_000,
            first_apron=209_015_000,
            second_apron=221_686_000,
            minimum_team_salary=148_465_000,
            source_name="test fixture",
        )
    )
    db.commit()


def test_quality_checks_flag_and_resolve_issues(db, cap_params, seasons):
    # cap_params fixture is for 2026-27, the configured cap league year. Statistics checks look
    # at the statistics season (2025-26), but cap availability is a cap-year question: the
    # 2026-27 row is present, so no cap finding — a missing 2025-26 cap row is not a problem.
    seasons(stats="2025-26", cap="2026-27")
    team = make_team(db, 1, "AAA")
    player = make_player(db, 100, "Fixture Guy", team, salary=50_000)
    db.add(
        PlayerSeasonStats(
            player_id=player.id,
            season="2025-26",
            stat_type="base",
            games_played=50,
            minutes=-3.0,
            stats={},
        )
    )
    contract = db.query(Contract).filter_by(player_id=player.id).one()
    db.add(ContractYear(contract_id=contract.id, season="1999-XX", salary=-5))
    db.commit()

    issues = validate_data(db)
    checks = {i["check"] for i in issues}
    assert "team_count" in checks  # only 1 team, not 30
    assert "negative_minutes" in checks
    assert "impossible_salary" in checks
    assert "cap_parameters_missing" not in checks

    # A second pass resolves the previous open issues and re-detects current ones
    validate_data(db)
    open_rows = db.query(DataQualityIssue).filter(DataQualityIssue.resolved_at.is_(None)).all()
    resolved_rows = (
        db.query(DataQualityIssue).filter(DataQualityIssue.resolved_at.isnot(None)).all()
    )
    assert open_rows and resolved_rows


def test_stale_detection(db, cap_params):
    from datetime import timedelta

    from app.db.models import Standing

    team = make_team(db, 1, "AAA")
    db.add(
        Standing(
            team_id=team.id,
            season="2025-26",
            wins=41,
            losses=41,
            win_pct=0.5,
            source_retrieved_at=datetime.now(UTC) - timedelta(days=30),
        )
    )
    db.commit()
    issues = validate_data(db)
    assert any(i["check"] == "stale_records" for i in issues)


STATS = "2025-26"
CAP = "2026-27"


@pytest.mark.parametrize(
    ("cap_rows", "stats", "cap", "missing"),
    [
        pytest.param([CAP], STATS, CAP, False, id="cap-year-only"),
        pytest.param([STATS], STATS, CAP, True, id="statistics-year-only"),
        pytest.param([CAP, STATS], STATS, CAP, False, id="both-years"),
        pytest.param([], STATS, CAP, True, id="neither-year"),
        pytest.param([STATS], STATS, STATS, False, id="same-year-present"),
    ],
)
def test_cap_parameters_are_checked_for_the_configured_cap_year(
    db, seasons, cap_rows, stats, cap, missing
):
    """Cap data is looked up by `cap_league_year` everywhere else (trade legality, the builder),
    so the quality check must ask the same question and name the same year."""
    seasons(stats=stats, cap=cap)
    for year in cap_rows:
        _add_cap_year(db, year)

    returned = [i for i in validate_data(db) if i["check"] == "cap_parameters_missing"]
    saved = (
        db.query(DataQualityIssue)
        .filter(
            DataQualityIssue.check_name == "cap_parameters_missing",
            DataQualityIssue.resolved_at.is_(None),
        )
        .all()
    )

    if not missing:
        assert returned == [] and saved == []
        return
    expected = f"no cap parameters loaded for league year {cap}; run `make seed-config`"
    assert [(i["severity"], i["message"]) for i in returned] == [("error", expected)]
    assert [(i.severity, i.message) for i in saved] == [("error", expected)]
    if stats != cap:
        assert stats not in returned[0]["message"]


@pytest.mark.parametrize(
    ("standing_season", "stale_expected"),
    [(STATS, True), (CAP, False)],
    ids=["statistics-year-standing", "cap-year-standing"],
)
def test_statistics_checks_keep_using_the_statistics_season(
    db, cap_params, seasons, standing_season, stale_expected
):
    """`stale_records` reads standings for `current_season`, not the cap year: a stale 2025-26
    standing is found while only a stale 2026-27 one (the cap year) is not."""
    from datetime import timedelta

    seasons(stats=STATS, cap=CAP)
    team = make_team(db, 1, "AAA")
    db.add(
        Standing(
            team_id=team.id,
            season=standing_season,
            wins=41,
            losses=41,
            win_pct=0.5,
            source_retrieved_at=datetime.now(UTC) - timedelta(days=30),
        )
    )
    db.commit()

    found = any(i["check"] == "stale_records" for i in validate_data(db))
    assert found is stale_expected
