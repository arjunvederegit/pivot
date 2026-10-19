# ruff: noqa: F811  (the imported `world` fixture is re-used as a parameter name)
"""R9.3A QA round 1 (financial correctness) — each test is a defect the independent reviewer reproduced.

The reviewer's headings are kept so a failure points at the finding. Fixtures reuse the R8.3 fake-provider
helpers, so the contract job runs end to end against live-shaped synthetic responses.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cba.builder import build_trade_context
from app.cba.engine import TradeLegalityEngine
from app.db.models import Contract, ContractYear, DataSyncRun
from app.ingestion import balldontlie_jobs as jobs
from app.services.financials import (
    horizon,
    player_financials,
    team_financial_summary,
    team_financials,
)
from app.services.trade_financials import UNCHECKED_CATEGORIES, trade_financial_analysis
from tests.conftest import make_player, make_team
from tests.integration.test_balldontlie_ingestion import (  # noqa: F401 (fixture)
    LEAGUE_YEAR,
    provider_for,
    world,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _sync(db: Session, world, mutate) -> dict:
    """Run the 2026-27 contract job after letting the test edit the fake provider's rows."""
    mutate(world["contracts"])
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    return jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)


# ------------------------------------------------------- F2 / F3: a read is only "complete" if it is clean


class TestACleanReadIsRequiredForTheHorizon:
    def test_the_clean_fixture_is_complete_and_counts_toward_the_horizon(self, db: Session, world) -> None:
        detail = _sync(db, world, lambda c: None)
        assert detail["outcome"] == "complete"
        assert horizon(db, LEAGUE_YEAR).seasons == (LEAGUE_YEAR,)

    def test_a_row_for_another_season_is_refused_and_the_season_is_partial(self, db: Session, world) -> None:
        def mutate(c):
            c[1][0]["season"] = 2031  # a 2031 row inside a 2026 request
        detail = _sync(db, world, mutate)
        assert detail["outcome"] == "partial" and detail["refused_rows_total"] == 1
        assert "season 2031-32 returned for a 2026-27 request" in detail["refused_rows"][0]["why"]
        assert db.scalar(select(ContractYear).where(ContractYear.salary == 30_000_000)) is None, "never stored as 2026-27"
        assert horizon(db, LEAGUE_YEAR).seasons == ()

    def test_a_non_positive_cap_hit_is_refused_not_priced(self, db: Session, world) -> None:
        def mutate(c):
            c[1][0]["cap_hit"] = 0
        detail = _sync(db, world, mutate)
        assert detail["outcome"] == "partial" and "non-positive cap hit 0" in detail["refused_rows"][0]["why"]
        assert db.scalar(select(ContractYear).where(ContractYear.salary <= 0)) is None

    def test_duplicate_rows_for_one_player_and_season_are_refused_not_last_one_wins(self, db: Session, world) -> None:
        def mutate(c):
            c[1].append({**c[1][0], "id": 99, "cap_hit": 31_000_000})
        detail = _sync(db, world, mutate)
        assert detail["outcome"] == "partial" and detail["refused_rows_total"] == 2
        assert db.scalar(select(ContractYear).where(ContractYear.salary.in_([30_000_000, 31_000_000]))) is None

    def test_a_malformed_row_makes_the_season_partial(self, db: Session, world) -> None:
        def mutate(c):
            c[1].append({"id": "x", "player_id": None, "season": "not-a-season"})
        detail = _sync(db, world, mutate)
        assert detail["outcome"] == "partial" and detail["read_warnings"]
        assert horizon(db, LEAGUE_YEAR).seasons == ()

    def test_an_empty_season_is_not_a_complete_season(self, db: Session, world) -> None:
        """An empty response for the whole season must not turn every later contract into 'ends'."""
        world["routes"]["/nba/v1/contracts/teams"] = lambda request: {"data": []}
        provider, _ = provider_for(world["routes"])
        jobs.sync_identities(db, provider)
        detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
        assert detail["outcome"] == "partial"
        assert horizon(db, LEAGUE_YEAR).seasons == ()


# ---------------------------------------- F2: a later season listed WITHOUT a cap hit means the length is unknown


def test_a_later_unpriced_provider_row_makes_the_contract_length_unknown(db: Session) -> None:
    from app.canonical.records import SINGLE_SEASON_CONTRACT_SOURCE

    team = make_team(db, 1, "AAA")
    p = make_player(db, 10, "Unpriced Later", team, salary=None, contract_type=None)
    contract = Contract(player_id=p.id, source_provider="balldontlie", source_name=SINGLE_SEASON_CONTRACT_SOURCE)
    db.add(contract)
    db.flush()
    db.add(ContractYear(contract_id=contract.id, season="2026-27", salary=5_000_000, salary_basis="cap_hit", source_provider="balldontlie"))
    for season in ("2026-27", "2027-28"):
        db.add(DataSyncRun(job_name="sync_contracts", status="succeeded", finished_at=NOW,
                           detail={"season": season, "outcome": "complete", "teams_failed": [], "rows_returned": 5,
                                   "unpriced_players": [{"player_id": p.id}] if season == "2027-28" else []}))
    db.commit()
    fin = player_financials(db, {p.id: p.full_name}, "2026-27")[p.id]
    assert fin.end_status == "unknown" and fin.years_remaining is None


# ---------------------------------------------------------------------------------- F5: freshness


def test_an_unpriced_record_has_no_retrieval_time_and_a_team_reports_its_oldest_priced_figure(db: Session) -> None:
    team = make_team(db, 1, "AAA")
    old, new = datetime(2026, 10, 18, 3, 0, tzinfo=UTC), datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    make_player(db, 10, "Priced", team, salary=9_000_000, contract_type=None)
    later = make_player(db, 11, "Later Only", team, salary=None, contract_type=None)
    for y in db.scalars(select(ContractYear)).all():
        y.source_retrieved_at = old
    c2 = Contract(player_id=later.id, source_provider="balldontlie", source_name="x")
    db.add(c2)
    db.flush()
    db.add(ContractYear(contract_id=c2.id, season="2027-28", salary=7_000_000, source_provider="balldontlie", source_retrieved_at=new))
    db.commit()
    fin = team_financials(db, team.id, "2026-27", "2025-26")
    unpriced = next(r for r in fin.unpriced if r.player_id == later.id)
    assert unpriced.retrieved_at is None
    assert team_financial_summary(fin, None)["retrieved_at"].startswith("2026-10-18")
    assert "later seasons do carry cap hits" in unpriced.status_reason


# ------------------------------------------------------------------ F6: gaps and horizon limits are unknown


def test_a_gap_or_a_season_beyond_the_horizon_is_unknown_never_a_known_zero(db: Session, cap_params) -> None:
    from app.canonical.records import SINGLE_SEASON_CONTRACT_SOURCE

    team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
    ra = [make_player(db, 100 + i, f"A{i}", team_a, salary=20_000_000, contract_type=None) for i in range(13)]
    rb = [make_player(db, 200 + i, f"B{i}", team_b, salary=10_000_000, contract_type=None) for i in range(13)]
    for pl, seasons in ((ra[0], ["2028-29"]), (rb[0], ["2027-28", "2028-29"])):  # ra[0]: rows 2026-27 + 2028-29 (a GAP)
        c = db.scalar(select(Contract).where(Contract.player_id == pl.id))
        c.source_name = SINGLE_SEASON_CONTRACT_SOURCE
        for season in seasons:
            db.add(ContractYear(contract_id=c.id, season=season, salary=10_000_000, salary_basis="cap_hit"))
    for season in ("2026-27", "2027-28", "2028-29"):
        db.add(DataSyncRun(job_name="sync_contracts", status="succeeded", finished_at=NOW, detail={"season": season, "outcome": "complete", "teams_failed": [], "rows_returned": 9}))
    db.commit()
    moves = [
        {"player_id": ra[0].id, "from_team_id": team_a.id, "to_team_id": team_b.id},
        {"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
    ]
    context = build_trade_context(db, [team_a.id, team_b.id], moves)
    analysis = trade_financial_analysis(context, TradeLegalityEngine().evaluate(context))
    seasons = {c["season"]: c for c in analysis["teams"][team_a.id]["commitments_by_season"]}
    assert seasons["2027-28"]["outgoing"]["complete"] is False, "a gap inside a contract is unknown"
    assert seasons["2027-28"]["net_known"] is None


# ------------------------------------------------------------------- F8: phantom movers and empty rosters


class TestAFreeAgentOrEmptyRosterCannotManufactureAVerifiedPayroll:
    def test_a_free_agent_named_in_a_deal_is_not_subtracted_from_the_senders_payroll(self, db: Session, cap_params) -> None:
        team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
        for i in range(14):
            make_player(db, 100 + i, f"A{i}", team_a, salary=10_000_000)
        rb = [make_player(db, 200 + i, f"B{i}", team_b, salary=10_000_000) for i in range(14)]
        free_agent = make_player(db, 900, "Free Agent", None, salary=30_000_000)  # on no roster
        moves = [
            {"player_id": free_agent.id, "from_team_id": team_a.id, "to_team_id": team_b.id},
            {"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
        ]
        context = build_trade_context(db, [team_a.id, team_b.id], moves)
        a = next(t for t in context.teams if t.team_id == team_a.id)
        assert a.coverage_after.known == 140_000_000 + 10_000_000, "AAA's payroll never contained the free agent"
        assert a.roster_count_after == 15, "and he was never in its roster count"

    def test_a_team_with_no_roster_has_no_after_coverage(self, db: Session, cap_params) -> None:
        team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")  # AAA has nobody on file
        rb = [make_player(db, 200 + i, f"B{i}", team_b, salary=10_000_000) for i in range(14)]
        moves = [{"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id}]
        context = build_trade_context(db, [team_a.id, team_b.id], moves)
        a = next(t for t in context.teams if t.team_id == team_a.id)
        assert a.coverage_after is None and a.payroll_after is None


# ----------------------------------------------------------------------- F4: cap-room confidence, unchecked list


def test_the_cap_room_pass_is_medium_confidence_and_cap_holds_are_listed_as_unchecked(db: Session, cap_params) -> None:
    team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
    for i in range(11):
        make_player(db, 100 + i, f"A{i}", team_a, salary=5_000_000)
    rb = [make_player(db, 200 + i, f"B{i}", team_b, salary=30_000_000 if i == 0 else 5_000_000) for i in range(13)]
    ra0 = db.scalar(select(Contract)).player_id
    moves = [
        {"player_id": ra0, "from_team_id": team_a.id, "to_team_id": team_b.id},
        {"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
    ]
    legality = TradeLegalityEngine().evaluate(build_trade_context(db, [team_a.id, team_b.id], moves))
    row = next(r for r in legality["rule_results"] if r["rule_code"] == "SALARY_MATCHING" and r["team_id"] == team_a.id)
    assert row["status"] == "pass" and row["confidence"] == "medium"
    assert "Cap holds and dead money" in [c["category"] for c in UNCHECKED_CATEGORIES]


# --------------------------------------------------------------------- F9 / F7: one set of numbers, one contract


class TestOneContractPerPlayerAndOneCoverage:
    @pytest.fixture()
    def client(self, db, cap_params):
        from fastapi.testclient import TestClient

        from app.db.base import get_db
        from app.main import app

        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    def test_the_legacy_counts_agree_with_the_financials_block(self, client, db) -> None:
        team = make_team(db, 1, "AAA")
        make_player(db, 10, "Priced", team, salary=9_000_000, contract_type=None)
        other_year = make_player(db, 11, "Other Year Only", team, salary=None, contract_type=None)
        c = Contract(player_id=other_year.id, source_provider="balldontlie", source_name="x")
        db.add(c)
        db.flush()
        db.add(ContractYear(contract_id=c.id, season="2027-28", salary=7_000_000))
        db.commit()
        body = client.get(f"/api/v1/teams/{team.id}/cap-outlook").json()
        assert body["players_with_contracts"] == body["financials"]["coverage"]["priced"] == 1
        assert body["complete"] is False

    def test_a_second_contract_row_is_not_summed_into_the_chart(self, client, db) -> None:
        team = make_team(db, 1, "AAA")
        p = make_player(db, 10, "Two Rows", team, salary=19_000_000, contract_type=None)
        extra = Contract(id="zzzz-later-id", player_id=p.id, source_provider="x", source_name="x")
        db.add(extra)
        db.flush()
        db.add(ContractYear(contract_id=extra.id, season="2026-27", salary=20_000_000))
        db.commit()
        body = client.get(f"/api/v1/teams/{team.id}/cap-outlook").json()
        season = next(s for s in body["seasons"] if s["season"] == "2026-27")
        assert season["total"] == body["financials"]["known_subtotal"], "chart and subtotal count the same contract"

    def test_activation_refuses_to_create_a_second_contract_for_a_player(self, tmp_path) -> None:
        from sqlalchemy import create_engine, text

        from app.db.models import Base, Player
        from app.services import provider_data_activation as act

        def make(path):
            url = f"sqlite:///{path}"
            engine = create_engine(url)
            Base.metadata.create_all(engine)
            with engine.begin() as conn:
                conn.execute(text("create table alembic_version (version_num varchar(32) not null)"))
                conn.execute(text("insert into alembic_version values ('x')"))
            with Session(engine) as s:
                s.add(Player(id="p1", nba_player_id=1, full_name="P", is_active=True))
                s.commit()
            return url, engine

        s_url, s_engine = make(tmp_path / "s.db")
        t_url, t_engine = make(tmp_path / "t.db")
        with Session(s_engine) as s:
            s.add(Contract(id="c-src", player_id="p1", source_provider="b", source_name="b"))
            s.commit()
        with Session(t_engine) as s:
            s.add(Contract(id="c-tgt", player_id="p1", source_provider="b", source_name="b"))
            s.commit()
        report = act.plan(tmp_path / "s.db", t_url)
        assert report["blocked"] is True
        assert any("already has a different contract row" in str(t["dangling_examples"]) for t in report["tables"])
