"""POST /trades is atomic: a request the API rejects leaves no trade records behind.

`create_trade` stages the proposal, its teams and its assets, and then `_trade_detail`
performs the validation that can reject the request (cap data, duplicate and phantom
moves, unknown scenario/player) before it persists rule results and evaluations. These
tests run the real request lifecycle against a file-backed SQLite database and observe
the outcome through a separate engine, so a commit made anywhere in the request is
visible and a rolled-back one is not. The shared in-memory `db` fixture cannot show this:
every session it hands out is the same connection, so nothing is ever "not committed".

All records are synthetic test fixtures. The database is created under pytest's
`tmp_path`; it is never an application database.

SQLite does not enforce foreign keys here (the app never enables `PRAGMA foreign_keys`),
so an unknown scenario or player id reaches the domain check instead of failing at the
database. PostgreSQL behaves differently and is not exercised by this module.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import get_db
from app.db.models import (
    Base,
    Contract,
    ContractYear,
    LeagueCapParameters,
    PlayerSeasonStats,
    Standing,
    TeamNeed,
    TeamSeasonStats,
    TradeAsset,
    TradeEvaluation,
    TradeProposal,
    TradeRuleResult,
    TradeTeam,
)
from tests.conftest import make_player, make_team

TRADE_TABLES = (TradeProposal, TradeTeam, TradeAsset, TradeRuleResult, TradeEvaluation)


# ------------------------------------------------------------------------ harness


class World:
    """A committed fixture league in a task-owned SQLite file, plus a request client."""

    def __init__(self, db_path, ids: dict, engine: Engine, client: TestClient) -> None:
        self.db_path = db_path
        self.ids = ids
        self.engine = engine
        self.client = client
        self.url = f"sqlite:///{db_path}"

    def snapshot(self) -> dict[str, set[str]]:
        """Primary keys of every trade table, read through a separate engine/connection."""
        observer = create_engine(self.url)
        try:
            with observer.connect() as conn:
                return {
                    model.__tablename__: set(conn.execute(select(model.id)).scalars())
                    for model in TRADE_TABLES
                }
        finally:
            observer.dispose()

    def rows(self, model, **where) -> list:
        observer = create_engine(self.url)
        try:
            with Session(observer) as session:
                return list(session.scalars(select(model).filter_by(**where)))
        finally:
            observer.dispose()


def _verified_disposable_path(tmp_path):
    path = tmp_path / "w22-trade-atomicity.db"
    assert not path.exists(), "the test database must be created fresh"
    assert not path.is_symlink()
    assert path.resolve().parent == tmp_path.resolve()
    # Never the app's configured database (the global engine is pinned in-memory).
    assert os.environ.get("DATABASE_URL") != f"sqlite:///{path}"
    return path


def _seed(engine: Engine, *, with_cap_params: bool) -> dict:
    """Committed fixture state, written through a setup session that is closed afterwards."""
    Base.metadata.create_all(engine)
    ids: dict = {}
    session = Session(engine, expire_on_commit=False)
    try:
        if with_cap_params:
            session.add(
                LeagueCapParameters(
                    league_year="2026-27",
                    salary_cap=164_961_000,
                    luxury_tax=200_428_000,
                    first_apron=209_015_000,
                    second_apron=221_686_000,
                    minimum_team_salary=148_465_000,
                    source_name="test fixture (values match official 2026-27 release)",
                )
            )
            session.commit()
        team_a = make_team(session, 1, "AAA", "Alpha Test Club")
        team_b = make_team(session, 2, "BBB", "Beta Test Club")
        a1 = make_player(session, 101, "Fixture Alpha One", team_a, salary=20_000_000)
        a2 = make_player(session, 102, "Fixture Alpha Two", team_a, salary=5_000_000)
        b1 = make_player(session, 201, "Fixture Beta One", team_b, salary=18_000_000)
        bench_a = [
            make_player(session, 300 + i, f"Fixture A Bench {i}", team_a, salary=3_000_000)
            for i in range(12)
        ]
        for i in range(12):
            make_player(session, 400 + i, f"Fixture B Bench {i}", team_b, salary=3_000_000)

        now = datetime.now(UTC)
        for team in (team_a, team_b):
            session.add(
                Standing(
                    team_id=team.id, season="2025-26", wins=41, losses=41, win_pct=0.5,
                    conference="East", source_retrieved_at=now,
                )
            )
            session.add(
                TeamSeasonStats(
                    team_id=team.id, season="2025-26", stat_type="advanced",
                    stats={"OFF_RATING": 114.0, "DEF_RATING": 114.0, "NET_RATING": 0.0,
                           "PACE": 99.0, "TS_PCT": 0.57},
                    source_retrieved_at=now,
                )
            )
            session.add(
                TeamSeasonStats(
                    team_id=team.id, season="2025-26", stat_type="base",
                    stats={"PTS": 114.0, "FG3A": 35.0, "STL": 7.5, "BLK": 5.0},
                    source_retrieved_at=now,
                )
            )
            session.add(
                TeamNeed(
                    team_id=team.id, season="2025-26", need_key="three_point_volume",
                    severity=0.6, percentile=20.0, explanation="test fixture need",
                )
            )
        for player in (a1, a2, b1):
            session.add(
                PlayerSeasonStats(
                    player_id=player.id, season="2025-26", stat_type="base", games_played=70,
                    minutes=30.0, stats={"PTS": 18.0, "REB": 5.0, "AST": 4.0},
                    source_retrieved_at=now,
                )
            )

        # A committed sentinel trade that no request may touch.
        sentinel = TradeProposal(name="sentinel", notes="pre-existing")
        session.add(sentinel)
        session.flush()
        session.add(TradeTeam(trade_id=sentinel.id, team_id=team_a.id))
        session.add(
            TradeAsset(
                trade_id=sentinel.id, asset_type="player", from_team_id=team_a.id,
                to_team_id=team_b.id, player_id=a2.id,
            )
        )
        session.add(
            TradeRuleResult(
                trade_id=sentinel.id, rule_code="SENTINEL", team_id=team_a.id, status="pass",
                message="sentinel",
            )
        )
        session.add(
            TradeEvaluation(
                trade_id=sentinel.id, team_id=team_a.id, legality_status="verified_legal",
                composite_utility=1.0,
            )
        )
        session.commit()
        ids.update(
            team_a=team_a.id, team_b=team_b.id, a1=a1.id, a2=a2.id, b1=b1.id,
            bench_a=[p.id for p in bench_a], sentinel=sentinel.id,
        )
    finally:
        session.close()
    return ids


@pytest.fixture()
def make_world(tmp_path):
    from app.core.cache import get_cache
    from app.main import app

    created: list[tuple[Engine, object]] = []
    previous = app.dependency_overrides.get(get_db)

    def build(*, with_cap_params: bool = True) -> World:
        get_cache().bump_data_version()  # skills are cached per data version
        db_path = _verified_disposable_path(tmp_path)
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        ids = _seed(engine, with_cap_params=with_cap_params)

        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        def request_session():
            session = factory()  # one new session per request, as in production `get_db`
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = request_session
        client = TestClient(app, raise_server_exceptions=False)
        created.append((engine, client))
        return World(db_path, ids, engine, client)

    yield build

    for engine, client in created:
        client.close()
        engine.dispose()
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous


def _payload(ids: dict, *, moves=None, team_ids=None, name="W2.2 trade", **extra) -> dict:
    a, b = ids["team_a"], ids["team_b"]
    if moves is None:
        moves = [
            {"player_id": ids["a1"], "from_team_id": a, "to_team_id": b},
            {"player_id": ids["b1"], "from_team_id": b, "to_team_id": a},
        ]
    return {"name": name, "team_ids": team_ids or [a, b], "player_moves": moves,
            "pick_moves": [], **extra}


def _error(response) -> dict:
    assert response.status_code != 201, response.text
    return response.json()["error"]


# --------------------------------------------------------------------- rejections

_SCENARIO_MISSING = str(uuid.uuid4())
_PLAYER_MISSING = str(uuid.uuid4())
_TEAM_MISSING = str(uuid.uuid4())


def _duplicate(ids):
    move = {"player_id": ids["a1"], "from_team_id": ids["team_a"], "to_team_id": ids["team_b"]}
    return _payload(ids, moves=[move, dict(move)])


def _phantom(ids):
    # a1 is rostered by team A, not B.
    return _payload(
        ids,
        moves=[{"player_id": ids["a1"], "from_team_id": ids["team_b"], "to_team_id": ids["team_a"]}],
    )


def _unknown_player(ids):
    return _payload(
        ids,
        moves=[{"player_id": _PLAYER_MISSING, "from_team_id": ids["team_a"],
                "to_team_id": ids["team_b"]}],
    )


def _unknown_scenario(ids):
    return _payload(ids, scenario_id=_SCENARIO_MISSING)


def _unknown_team(ids):
    return _payload(ids, moves=[], team_ids=[ids["team_a"], _TEAM_MISSING])


def _valid(ids):
    return _payload(ids)


REJECTIONS = [
    pytest.param(_duplicate, True, 422, "validation_error",
                 "the same player appears in more than one move", id="duplicate-player-move"),
    pytest.param(_phantom, True, 422, "validation_error",
                 "not on the roster they are being traded from", id="phantom-move"),
    pytest.param(_unknown_player, True, 404, "not_found", "Unknown player id",
                 id="unknown-player"),
    pytest.param(_unknown_scenario, True, 404, "not_found", "scenario ", id="unknown-scenario"),
    pytest.param(_unknown_team, True, 404, "not_found", "team ", id="unknown-team"),
    pytest.param(_valid, False, 409, "data_unavailable", "No cap parameters loaded",
                 id="missing-cap-parameters"),
]


@pytest.mark.parametrize(("build_payload", "with_cap", "status", "code", "fragment"), REJECTIONS)
def test_a_rejected_creation_leaves_no_trade_records(
    make_world, build_payload, with_cap, status, code, fragment
):
    world = make_world(with_cap_params=with_cap)
    before = world.snapshot()
    assert len(before["trade_proposals"]) == 1  # the sentinel

    response = world.client.post("/api/v1/trades", json=build_payload(world.ids))

    error = _error(response)
    assert response.status_code == status
    assert error["code"] == code
    assert fragment in error["message"]
    assert world.snapshot() == before


def test_the_unknown_ids_in_the_rejection_messages_are_the_requested_ones(make_world):
    world = make_world()
    scenario = _error(world.client.post("/api/v1/trades", json=_unknown_scenario(world.ids)))
    assert _SCENARIO_MISSING in scenario["message"]
    team = _error(world.client.post("/api/v1/trades", json=_unknown_team(world.ids)))
    assert _TEAM_MISSING in team["message"]
    player = _error(world.client.post("/api/v1/trades", json=_unknown_player(world.ids)))
    assert _PLAYER_MISSING in player["message"]


def test_an_unexpected_failure_before_the_final_commit_leaves_no_partial_trade(
    make_world, monkeypatch
):
    """Inject a failure in the evaluation helper, after validation has passed."""
    world = make_world()
    before = world.snapshot()

    def fail(self, *args, **kwargs):
        raise RuntimeError("induced evaluation failure")

    monkeypatch.setattr("app.api.v1.trades.EvaluationService.evaluate_for_team", fail)
    response = world.client.post("/api/v1/trades", json=_valid(world.ids))

    assert response.status_code == 500
    assert world.snapshot() == before


# ----------------------------------------------------------------------- successes


def test_a_valid_trade_is_created_with_its_complete_records(make_world):
    world = make_world()
    before = world.snapshot()
    ids = world.ids

    response = world.client.post("/api/v1/trades", json=_valid(ids))

    assert response.status_code == 201, response.text
    body = response.json()
    after = world.snapshot()
    assert all(before[t] <= after[t] for t in after)  # the sentinel records survive
    new ={t: after[t] - before[t] for t in after}
    assert new["trade_proposals"] == {body["id"]}
    assert len(new["trade_teams"]) == 2
    assert len(new["trade_assets"]) == 2
    assert len(new["trade_rule_results"]) == len(body["legality"]["rule_results"]) > 0
    assert len(new["trade_evaluations"]) == 2

    assert body["legality"]["overall_status"] != "verified_illegal"
    assert set(body["evaluations"]) == {ids["team_a"], ids["team_b"]}
    stored = {e.team_id: e for e in world.rows(TradeEvaluation, trade_id=body["id"])}
    for team_id, evaluation in body["evaluations"].items():
        assert stored[team_id].composite_utility == evaluation["composite_utility"]
        assert stored[team_id].legality_status == evaluation["legality"]["status"]
    stored_rules = world.rows(TradeRuleResult, trade_id=body["id"])
    assert {r.rule_code for r in stored_rules} == {
        r["rule_code"] for r in body["legality"]["rule_results"]
    }

    # The shared detail path reads the saved trade back unchanged.
    saved = world.client.get(f"/api/v1/trades/{body['id']}")
    assert saved.status_code == 200
    assert saved.json()["legality"]["overall_status"] == body["legality"]["overall_status"]
    assert world.snapshot() == after  # GET persisted nothing


def test_a_well_formed_but_cap_illegal_draft_is_still_saved(make_world):
    """Illegal is not rejected: a draft the CBA engine fails is persisted, with its failing
    rule results, and its decision score stays suppressed."""
    world = make_world()
    before = world.snapshot()
    ids = world.ids
    giveaway = [
        {"player_id": pid, "from_team_id": ids["team_a"], "to_team_id": ids["team_b"]}
        for pid in [ids["a2"], *ids["bench_a"][:3]]
    ]

    response = world.client.post("/api/v1/trades", json=_payload(ids, moves=giveaway))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legality"]["overall_status"] == "verified_illegal"
    after = world.snapshot()
    new = {t: after[t] - before[t] for t in after}
    assert new["trade_proposals"] == {body["id"]}
    assert len(new["trade_teams"]) == 2
    assert len(new["trade_assets"]) == len(giveaway)
    assert len(new["trade_evaluations"]) == 2
    failing = [r for r in world.rows(TradeRuleResult, trade_id=body["id"]) if r.status == "fail"]
    assert any(r.rule_code == "ROSTER_SIZE" for r in failing)
    stored = {e.team_id: e for e in world.rows(TradeEvaluation, trade_id=body["id"])}
    assert stored[ids["team_a"]].legality_status == "verified_illegal"  # 10 players left
    for team_id, evaluation in stored.items():
        assert evaluation.legality_status == body["evaluations"][team_id]["legality"]["status"]
        assert evaluation.composite_utility is None  # the overall verdict gates every team
    for evaluation in body["evaluations"].values():
        assert evaluation["composite_utility"] is None
        assert evaluation["decision_status"] == "suppressed_illegal"


def _set_salary(world: World, player_id: str, salary: int) -> None:
    """Commit a different 2026-27 salary for one fixture player, before the request."""
    with Session(world.engine) as session:
        year = session.scalars(
            select(ContractYear)
            .join(Contract, ContractYear.contract_id == Contract.id)
            .where(Contract.player_id == player_id, ContractYear.season == "2026-27")
        ).one()
        year.salary = salary
        session.commit()


def test_a_salary_matching_failure_is_saved_as_an_illegal_draft(make_world):
    """A one-for-one swap with valid rosters that fails SALARY_MATCHING is still created.

    Team AAA (payroll $61M) sends Alpha One ($20M) and takes back Beta One at $130M, so its
    post-trade payroll of $171M is over the $164.961M cap and it needs a matching band. Below
    the first apron the expanded band allows $20M + ~$9.70M, far under the $130M incoming, so
    the rule fails. Rosters stay 14 and 13 (no ROSTER_SIZE failure). Team BBB ends at $38M,
    under the cap, so it needs no matching.
    """
    world = make_world()
    ids = world.ids
    _set_salary(world, ids["b1"], 130_000_000)
    before = world.snapshot()

    response = world.client.post("/api/v1/trades", json=_valid(ids))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["legality"]["overall_status"] == "verified_illegal"
    results = body["legality"]["rule_results"]
    matching = {r["team_id"]: r for r in results if r["rule_code"] == "SALARY_MATCHING"}
    assert matching[ids["team_a"]]["status"] == "fail"
    calc = matching[ids["team_a"]]["calculation"]
    assert (calc["outgoing_salary"], calc["incoming_salary"]) == (20_000_000, 130_000_000)
    assert calc["post_trade_payroll"] > 164_961_000  # over the cap, so matching applies
    assert calc["maximum_incoming"] < calc["incoming_salary"]
    assert not [r for r in results if r["rule_code"] == "ROSTER_SIZE" and r["status"] == "fail"]
    teams = body["legality"]["teams"]
    assert (teams[ids["team_a"]]["roster_before"], teams[ids["team_a"]]["roster_after"]) == (14, 14)
    assert (teams[ids["team_b"]]["roster_before"], teams[ids["team_b"]]["roster_after"]) == (13, 13)

    # Scoring is suppressed for every team, naming the failing rule.
    for evaluation in body["evaluations"].values():
        assert evaluation["composite_utility"] is None
        assert evaluation["decision_status"] == "suppressed_illegal"
        failing = evaluation["suppression"]["failing_rules"]
        assert any(r["rule_code"] == "SALARY_MATCHING" for r in failing)

    # What a separate connection sees once the request session has closed.
    after = world.snapshot()
    assert all(before[t] <= after[t] for t in after)  # the sentinel records survive
    new = {t: after[t] - before[t] for t in after}
    assert new["trade_proposals"] == {body["id"]}
    assert len(new["trade_teams"]) == 2
    assert len(new["trade_assets"]) == 2
    assert len(new["trade_rule_results"]) == len(results) > 0
    assert len(new["trade_evaluations"]) == 2
    stored_rules = world.rows(TradeRuleResult, trade_id=body["id"])
    failed = {(r.rule_code, r.team_id) for r in stored_rules if r.status == "fail"}
    assert ("SALARY_MATCHING", ids["team_a"]) in failed
    assert not [r for r in stored_rules if r.rule_code == "ROSTER_SIZE" and r.status == "fail"]
    stored = {e.team_id: e for e in world.rows(TradeEvaluation, trade_id=body["id"])}
    assert stored[ids["team_a"]].legality_status == "verified_illegal"
    for team_id, evaluation in stored.items():
        assert evaluation.legality_status == body["evaluations"][team_id]["legality"]["status"]
        assert evaluation.composite_utility is None
    sentinel = world.rows(TradeProposal, id=ids["sentinel"])
    assert [(p.name, p.notes) for p in sentinel] == [("sentinel", "pre-existing")]
    assert [e.composite_utility for e in world.rows(TradeEvaluation, trade_id=ids["sentinel"])] == [1.0]
