# ruff: noqa: F811  (the imported `world` fixture is re-used as a parameter name)
"""R9.3A QA round 4 (adversarial end-to-end) — each test is a defect or a surviving planted defect the reviewer reported."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.cba.builder import build_trade_context
from app.cba.engine import TradeLegalityEngine
from app.db.models import (
    Base,
    CandidateEstimate,
    DataSyncRun,
    Injury,
    ModelVersion,
    Player,
    ProviderEntityId,
)
from app.ingestion import balldontlie_jobs as jobs
from app.services import provider_data_activation as act
from app.services.financials import horizon
from app.services.injury_status import injury_status
from app.services.trade_financials import legality_assessment, trade_financial_analysis
from tests.conftest import make_player, make_team
from tests.integration.test_balldontlie_ingestion import (  # noqa: F401
    LEAGUE_YEAR,
    bdl_player,
    provider_for,
    world,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


# ------------------------------------------------- F1: a dropped team must not read as contracts that end early


def test_previously_priced_contracts_that_vanish_make_the_reread_partial(db: Session, world, monkeypatch) -> None:
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    assert jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)["outcome"] == "complete"
    monkeypatch.setattr(jobs, "NOT_RESEEN_PARTIAL_THRESHOLD", 1)
    world["contracts"][2] = []  # BOS answers 200 with nothing
    again = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert again["outcome"] == "partial" and again["dropped_many_previously_priced"] is True and again["not_reseen"] == 1
    # the latest run is partial, so the season is not newly counted from it (the earlier clean run still stands)
    assert horizon(db, LEAGUE_YEAR).seasons == (LEAGUE_YEAR,)
    runs = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_contracts").order_by(DataSyncRun.started_at)).all()
    assert runs[-1].detail["outcome"] == "partial"


def test_a_single_waiver_is_tolerated(db: Session, world) -> None:
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    world["contracts"][2] = []
    assert jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)["outcome"] == "complete"


def test_an_empty_injury_feed_does_not_close_every_open_injury(db: Session, world) -> None:
    feed = {"data": [{"player": bdl_player(11, "Alex", "Fixture", 1), "return_date": None, "description": None, "status": "Out"}], "meta": {"per_page": 100}}
    routes = {**world["routes"], "/nba/v1/player_injuries": lambda r: feed}
    provider, _ = provider_for(routes)
    jobs.sync_identities(db, provider)
    jobs.sync_injuries_from_balldontlie(db, provider)
    feed["data"] = []
    with pytest.raises(jobs.ProviderSyncError, match="refusing to close"):
        jobs.sync_injuries_from_balldontlie(db, provider)
    assert db.scalar(select(Injury).where(Injury.valid_to.is_not(None))) is None


# -------------------------------------------------------------------------- F2-F5: the activation tool


def _db(path: Path, revision: str | None = "a7d3e1f9c204") -> str:
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    if revision:
        with engine.begin() as conn:
            conn.execute(text("create table alembic_version (version_num varchar(32) not null)"))
            conn.execute(text("insert into alembic_version values (:r)"), {"r": revision})
    with Session(engine) as s:
        s.add(Player(id="p1", nba_player_id=1, full_name="P", is_active=True))
        s.commit()
    engine.dispose()
    return url


def test_a_unique_key_collision_is_reported_by_the_dry_run_not_discovered_by_the_apply(tmp_path: Path) -> None:
    s_url, t_url = _db(tmp_path / "s.db"), _db(tmp_path / "t.db")
    for url, rid in ((s_url, "src"), (t_url, "tgt")):
        with Session(create_engine(url)) as s:
            s.add(ProviderEntityId(id=rid, entity_type="player", provider="balldontlie", provider_id="9", internal_id="p1", match_method="x", confidence="high", evidence={}, observed_at=NOW))
            s.commit()
    report = act.plan(tmp_path / "s.db", t_url)
    assert report["blocked"] is True
    assert any("already exists in the target under a different id" in str(t["dangling_examples"]) for t in report["tables"])
    with pytest.raises(act.ActivationRefused):
        act.apply(tmp_path / "s.db", t_url, tmp_path / "bk")


def test_missing_revisions_are_refused_not_treated_as_equal(tmp_path: Path) -> None:
    _db(tmp_path / "s.db", revision=None)
    t_url = _db(tmp_path / "t.db", revision=None)
    with pytest.raises(act.ActivationRefused, match="no alembic_version"):
        act.plan(tmp_path / "s.db", t_url)


def test_an_estimate_under_a_serving_model_is_never_copied(tmp_path: Path) -> None:
    s_url, t_url = _db(tmp_path / "s.db"), _db(tmp_path / "t.db")
    for url in (s_url, t_url):
        with Session(create_engine(url)) as s:
            s.add(ModelVersion(id="serving", model_name="player_impact", version="live", algorithm="a", status="active", is_active=True))
            s.commit()
    with Session(create_engine(s_url)) as s:
        s.add(ModelVersion(id="cand", model_name="player_forecast_fg3_pct", version="cand-x", algorithm="a", status="candidate", is_active=False))
        s.flush()
        s.add(CandidateEstimate(id="e1", model_version_id="cand", player_id="p1", season="2025-26", target_season="2026-27", target="fg3_pct", output_kind="projected", estimate=0.3))
        s.add(CandidateEstimate(id="e2", model_version_id="serving", player_id="p1", season="2025-26", target_season="2026-27", target="fg3_pct", output_kind="projected", estimate=0.9))
        s.commit()
    result = act.apply(tmp_path / "s.db", t_url, tmp_path / "bk")
    assert result["written"]["candidate_estimates"] == 1
    con = sqlite3.connect(tmp_path / "t.db")
    assert {r[0] for r in con.execute("select id from candidate_estimates")} == {"e1"}
    con.close()


def test_an_unwritable_backup_directory_is_a_refusal_not_a_traceback(tmp_path: Path) -> None:
    _db(tmp_path / "s.db")
    t_url = _db(tmp_path / "t.db")
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    with pytest.raises(act.ActivationRefused, match="backup directory"):
        act.apply(tmp_path / "s.db", t_url, not_a_dir)


def test_a_backup_that_does_not_verify_stops_the_activation(tmp_path: Path, monkeypatch) -> None:
    """Planted defect N08: a backup whose rows differ from the target must stop everything and leave no file."""
    _db(tmp_path / "s.db")
    t_url = _db(tmp_path / "t.db")
    answers = iter([{"players": 1}, {"players": 2}])  # the backup's counts, then the target's: they differ
    monkeypatch.setattr(act, "_row_counts", lambda con: next(answers))
    with pytest.raises(act.ActivationRefused, match="did not verify"):
        act.apply(tmp_path / "s.db", t_url, tmp_path / "bk")
    assert list((tmp_path / "bk").glob("*.db")) == []


# ---------------------------------------------------------------------------------- F7 / F8: older schema and settings


def test_an_older_schema_does_not_turn_cap_outlook_into_a_500(db: Session, cap_params, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.api.v1 import teams as teams_api
    from app.db.base import get_db
    from app.main import app

    team = make_team(db, 1, "AAA")
    p = make_player(db, 10, "P", team, salary=9_000_000, contract_type=None, season="2026-27")

    def boom(*a, **k):
        raise OperationalError("select", {}, Exception("no such column: model_versions.status"))

    monkeypatch.setattr(teams_api, "tool_seasons", boom)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get(f"/api/v1/teams/{team.id}/cap-outlook")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200 and r.json()["financials"]["roster_basis"] is None and p.id


def test_a_malformed_cap_league_year_fails_at_startup_not_in_a_request() -> None:
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(cap_league_year="banana")
    assert Settings(cap_league_year=" 2027-28 ").cap_league_year == "2027-28"


# ----------------------------------------------------------------------------- F13 and surviving planted defects


def test_a_future_retrieval_time_is_not_fresh(db: Session) -> None:
    p = make_player(db, 1, "Odd", make_team(db, 1, "AAA"))
    db.add(Injury(player_id=p.id, status="Out", source_provider="balldontlie", source_retrieved_at=NOW + timedelta(hours=72)))
    db.commit()
    assert injury_status(db, p.id, now=NOW)["freshness"] == "stale"


def test_a_reading_just_over_a_day_old_is_stale_not_current(db: Session) -> None:
    p = make_player(db, 1, "Old", make_team(db, 1, "AAA"))
    db.add(Injury(player_id=p.id, status="Out", source_provider="balldontlie", source_retrieved_at=NOW - timedelta(hours=25)))
    db.commit()
    assert injury_status(db, p.id, now=NOW)["freshness"] == "stale"


def test_the_feed_never_read_is_said_plainly(db: Session) -> None:
    p = make_player(db, 1, "Fine", make_team(db, 1, "AAA"))
    out = injury_status(db, p.id, now=NOW)
    assert "never read an injury feed" in out["reason"]


def test_a_warning_is_unverified_not_a_checked_pass() -> None:
    la = legality_assessment(
        {"overall_status": "verified_legal", "rule_results": [
            {"rule_code": "MINIMUM_TEAM_SALARY", "status": "warning", "team_id": "t", "message": "under the floor", "calculation": {}},
            {"rule_code": "SALARY_MATCHING", "status": "pass", "team_id": "t", "message": "ok", "calculation": {}},
        ]},
        {"t": "AAA"},
    )
    assert la["counts"] == {"checked_passed": 1, "failed": 0, "unverified": 1}
    assert la["verdict"] == "indeterminate", "a warning must never let the assessment read as passing"


@pytest.mark.parametrize("extra,expected", [(0, "pass"), (1, "fail")])
def test_the_cap_room_boundary_is_exact(db: Session, cap_params, extra: int, expected: str) -> None:
    """Post-trade payroll AT the cap needs no matching; one dollar over does (planted defect M14: +$10M of slack)."""
    team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
    ra = [make_player(db, 100 + i, f"A{i}", team_a, salary=5_000_000 if i == 0 else 10_000_000) for i in range(14)]  # 135M
    rb = [make_player(db, 200 + i, f"B{i}", team_b, salary=164_961_000 - 130_000_000 + extra if i == 0 else 1_000_000) for i in range(13)]
    moves = [
        {"player_id": ra[0].id, "from_team_id": team_a.id, "to_team_id": team_b.id},
        {"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
    ]
    legality = TradeLegalityEngine().evaluate(build_trade_context(db, [team_a.id, team_b.id], moves))
    row = next(r for r in legality["rule_results"] if r["rule_code"] == "SALARY_MATCHING" and r["team_id"] == team_a.id)
    assert row["calculation"]["post_trade_payroll"] == 164_961_000 + extra
    assert row["status"] == expected


def test_the_apron_status_after_is_verified_only_never_built_from_a_floor(db: Session, cap_params) -> None:
    """Planted defect N14: apron_status_after filled from the at-least value."""
    team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
    ra = [make_player(db, 100 + i, f"A{i}", team_a, salary=40_000_000 if i < 6 else None, contract_type=None) for i in range(13)]
    rb = [make_player(db, 200 + i, f"B{i}", team_b, salary=10_000_000, contract_type=None) for i in range(13)]
    moves = [
        {"player_id": ra[0].id, "from_team_id": team_a.id, "to_team_id": team_b.id},
        {"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
    ]
    context = build_trade_context(db, [team_a.id, team_b.id], moves)
    legality = TradeLegalityEngine().evaluate(context)
    block = trade_financial_analysis(context, legality)["teams"][team_a.id]
    assert block["apron_status_after"] is None, "a floor is not a verified apron position"


def test_a_shot_zone_total_four_points_off_is_withheld(db: Session) -> None:
    """Planted defect N02: tolerance 0.4 → 4.0 would let a 1.0-per-game unit mismatch through."""
    from app.db.models import ProviderStatSnapshot
    from app.services.shot_zones import shot_zone_profile

    p = make_player(db, 1, "Z", make_team(db, 1, "AAA"))
    zone = {f"{k}_fga": 3.0 for k in ("restricted_area", "in_the_paint_(non-ra)", "mid-range", "left_corner_3", "right_corner_3", "above_the_break_3")}
    zone.update({f"{k}_fgm": 1.0 for k in ("restricted_area", "in_the_paint_(non-ra)", "mid-range", "left_corner_3", "right_corner_3", "above_the_break_3")})
    zone["backcourt_fga"] = 0.0
    for group, vals, agg in (("shooting/by_zone", zone, "unspecified"), ("general/base", {"fga": 17.0, "gp": 50}, "per_game")):  # 18.0 vs 17.0
        db.add(ProviderStatSnapshot(provider="balldontlie", entity_type="player", internal_id=p.id, season="2025-26", season_type="regular", stat_group=group, aggregation=agg, games_played=50 if group == "general/base" else None, values=vals, retrieved_at=NOW))
    db.commit()
    assert shot_zone_profile(db, p.id)["available"] is False


def test_the_two_way_salary_must_be_positive(db: Session, cap_params) -> None:
    from app.services.financials import two_way_salary

    for bad in (0, -5, None, "x"):
        cap_params.extras = {"two_way_salary": bad}
        db.commit()
        assert two_way_salary(db, "2026-27") is None


def test_a_zero_row_or_refused_season_does_not_count_toward_the_horizon(db: Session) -> None:
    for season, detail in (
        ("2026-27", {"outcome": "complete", "teams_failed": [], "rows_returned": 5}),
        ("2027-28", {"outcome": "complete", "teams_failed": [], "rows_returned": 0}),
    ):
        db.add(DataSyncRun(job_name="sync_contracts", status="succeeded", finished_at=NOW, detail={"season": season, **detail}))
    db.add(DataSyncRun(job_name="sync_contracts", status="succeeded", finished_at=NOW, detail={"season": "2028-29", "outcome": "complete", "teams_failed": [], "rows_returned": 5, "refused_rows_total": 1}))
    db.commit()
    assert horizon(db, "2026-27").seasons == ("2026-27",)
