"""The trial-evaluation harness, against a SYNTHETIC mini-league and a mock upstream.

These tests verify the harness's own logic — probe wiring, comparison arithmetic, the request
budget, tier skipping, response caching/replay and credential hygiene. They say nothing about
the real provider; the harness has never run against it.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.canonical.records import ProviderStatus
from app.db.models import Contract, ContractYear, PlayerSeasonStats, Standing
from app.integrations.balldontlie import evaluation as ev
from app.integrations.balldontlie.client import BalldontlieClient
from app.integrations.balldontlie.provider import BalldontlieProvider
from tests.conftest import make_player, make_team

KEY = "not-a-real-key-aaaaaaaa"  # fixture value; low entropy on purpose (secret scanners)

ATL = {"id": 1, "abbreviation": "ATL", "full_name": "Atlanta Hawks", "city": "Atlanta", "name": "Hawks"}
BOS = {"id": 2, "abbreviation": "BOS", "full_name": "Boston Celtics", "city": "Boston", "name": "Celtics"}


def bdl_player(i: int, first: str, last: str, team: dict, draft_year: int = 2019) -> dict:
    return {
        "id": i, "first_name": first, "last_name": last, "position": "G", "height": "6-4",
        "weight": "200", "draft_year": draft_year, "draft_round": 1, "draft_number": 5, "team": team,
    }  # fmt: skip


class MiniUpstream:
    """Routes keyed by path -> payload or callable(request) -> payload."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"error": "no route"})
        if isinstance(route, int):
            return httpx.Response(route, json={"error": "scripted"})
        payload = route(request) if callable(route) else route
        return httpx.Response(200, json=payload)


def provider_for(upstream: MiniUpstream, tier: str = "goat") -> BalldontlieProvider:
    return BalldontlieProvider(
        BalldontlieClient(
            KEY, tier=tier, max_retries=0, transport=httpx.MockTransport(upstream), sleep=lambda s: None
        )
    )


@pytest.fixture()
def world(db: Session):
    """Two teams, four players with 2025-26 per-game rows, standings and a contract snapshot."""
    atl = make_team(db, 1610612737, "ATL", "Atlanta Hawks")
    bos = make_team(db, 1610612738, "BOS", "Boston Celtics")
    people = {
        "a": make_player(db, 1001, "Ann Alpha", atl),
        "b": make_player(db, 1002, "Ben Bravo", atl),
        "c": make_player(db, 1003, "Cal Charlie", bos),
        "d": make_player(db, 1004, "Dee Delta", bos),
    }
    for index, player in enumerate(people.values()):
        player.draft_year = 2019
        db.add(
            PlayerSeasonStats(
                player_id=player.id, team_id=atl.id, season="2025-26", season_type="regular",
                stat_type="base", games_played=60 + index, minutes=30.0,
                stats={"PTS": 10.0 + index * 5, "REB": 4.0, "AST": 3.0},
                source_provider="nba_api", source_retrieved_at=datetime.now(UTC),
            )
        )  # fmt: skip
    for team, wins in ((atl, 40), (bos, 55)):
        db.add(
            Standing(team_id=team.id, season="2025-26", wins=wins, losses=82 - wins, win_pct=wins / 82)
        )
    db.commit()
    return {"atl": atl, "bos": bos, **people}


def active_players_route() -> dict:
    rows = [
        bdl_player(11, "Ann", "Alpha", ATL),
        bdl_player(12, "Ben", "Bravo", ATL),
        bdl_player(13, "Cal", "Charlie", BOS),
        bdl_player(14, "Dee", "Delta", BOS),
    ]
    return {"data": rows, "meta": {}}


def teams_route() -> dict:
    return {"data": [ATL, BOS]}


def averages_route(multiplier: float = 1.0):
    def route(request: httpx.Request) -> dict:
        rows = []
        for bdl_id, index in ((11, 0), (12, 1), (13, 2), (14, 3)):
            gp = 60 + index
            pts = (10.0 + index * 5) * (gp if multiplier != 1.0 else 1)
            rows.append(
                {"player_id": bdl_id, "season": 2025, "season_type": "regular",
                 "stats": {"gp": gp, "pts": pts, "reb": 4.0}}
            )  # fmt: skip
        return {"data": rows, "meta": {}}

    return route


def run(upstream, db, tmp_path, *, only=None, budget=200, tier="goat", replay=False, provider=None):
    return ev.run_plan(
        provider or provider_for(upstream, tier),
        db,
        tmp_path / "eval",
        budget=budget,
        only=only,
        replay_only=replay,
    )


def finding(result: dict, probe: str) -> dict:
    return next(f for f in result["findings"] if f["probe"] == probe)


# ------------------------------------------------------------------------------ plan


def test_plan_summary_costs_the_trial_without_a_request():
    plan = ev.plan_summary("trial")
    assert plan["estimated_requests"] == sum(s["estimated_requests"] for s in plan["steps"])
    assert plan["estimated_requests"] < 200
    assert plan["paced_minutes"] == pytest.approx(plan["estimated_requests"] / 4.0, abs=0.1)
    assert all(s["allowed_on_tier"] for s in plan["steps"])


def test_the_free_tier_plan_flags_what_it_cannot_run():
    plan = ev.plan_summary("free")
    blocked = {s["id"] for s in plan["steps"] if not s["allowed_on_tier"]}
    assert {"P9", "P5", "P6"} <= blocked


def test_thresholds_are_declared_up_front():
    assert ev.THRESHOLDS["active_player_link_rate"] == 0.95
    assert all(0 < v <= 1 for v in ev.THRESHOLDS.values())


# --------------------------------------------------------------------------- probes


def test_identity_probe_links_teams_and_players(db, world, tmp_path):
    upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route()})
    result = run(upstream, db, tmp_path, only={"P1-P2"})
    assert finding(result, "P1")["status"] == "pass"
    p2 = finding(result, "P2")
    assert p2["status"] == "pass" and p2["metrics"]["linked"] == 4


def test_identity_probe_fails_when_too_many_players_do_not_resolve(db, world, tmp_path):
    rows = active_players_route()
    rows["data"].append(bdl_player(15, "Never", "Seen", ATL, draft_year=2001))
    rows["data"].append(bdl_player(16, "Also", "Unknown", BOS, draft_year=2002))
    upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": rows})
    result = run(upstream, db, tmp_path, only={"P1-P2"})
    p2 = finding(result, "P2")
    assert p2["status"] == "fail" and p2["metrics"]["unresolved"] == 2


def test_standings_probe_compares_records(db, world, tmp_path):
    good = {
        "data": [
            {"team": ATL, "wins": 40, "losses": 42, "season": 2025},
            {"team": BOS, "wins": 55, "losses": 27, "season": 2025},
        ]
    }
    upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route(), "/nba/v1/standings": good})
    result = run(upstream, db, tmp_path, only={"P1-P2", "P4"})
    assert finding(result, "P4")["status"] == "pass"
    bad = {"data": [{"team": ATL, "wins": 1, "losses": 1, "season": 2025}]}
    upstream.routes["/nba/v1/standings"] = bad
    result = run(upstream, db, tmp_path / "again", only={"P4"})
    assert finding(result, "P4")["status"] == "fail"


def test_season_average_probe_recognises_per_game_figures(db, world, tmp_path):
    upstream = MiniUpstream(
        {
            "/nba/v1/teams": teams_route(),
            "/nba/v1/players/active": active_players_route(),
            "/nba/v1/season_averages/general": averages_route(1.0),
        }
    )
    result = run(upstream, db, tmp_path, only={"P1-P2", "P5"})
    p5 = finding(result, "P5")
    assert p5["status"] == "pass" and p5["metrics"]["inferred_aggregation"] == "per_game"
    assert p5["metrics"]["compared"] == 4


def test_season_average_probe_detects_totals_masquerading_as_averages(db, world, tmp_path):
    upstream = MiniUpstream(
        {
            "/nba/v1/teams": teams_route(),
            "/nba/v1/players/active": active_players_route(),
            "/nba/v1/season_averages/general": averages_route(multiplier=2.0),
        }
    )
    result = run(upstream, db, tmp_path, only={"P1-P2", "P5"})
    p5 = finding(result, "P5")
    assert p5["status"] == "fail"
    assert p5["metrics"]["inferred_aggregation"] == "totals"


def test_contract_probe_measures_roster_coverage_and_value_agreement(db, world, tmp_path):
    for player, salary in ((world["a"], 30_000_000), (world["b"], 20_000_000)):
        contract = Contract(player_id=player.id, source_name="snapshot", source_provider="bbref_snapshot")
        db.add(contract)
        db.flush()
        db.add(ContractYear(contract_id=contract.id, season="2026-27", salary=salary))
    db.commit()

    def contracts_for(team_rows):
        return lambda request: {"data": team_rows}

    atl_rows = [
        {"player_id": 11, "season": 2026, "team_id": 1, "cap_hit": 30_100_000},   # within 2 %
        {"player_id": 12, "season": 2026, "team_id": 1, "cap_hit": 31_000_000},   # 55 % off
    ]
    bos_rows = [
        {"player_id": 13, "season": 2026, "team_id": 2, "cap_hit": 9_000_000},
        {"player_id": 14, "season": 2026, "team_id": 2, "cap_hit": None},
    ]
    team_param = {1: atl_rows, 2: bos_rows}
    upstream = MiniUpstream(
        {
            "/nba/v1/teams": teams_route(),
            "/nba/v1/players/active": active_players_route(),
            "/nba/v1/contracts/teams": lambda r: {"data": team_param[int(r.url.params["team_id"])]},
        }
    )
    result = run(upstream, db, tmp_path, only={"P1-P2", "P9"})
    p9, p9b = finding(result, "P9"), finding(result, "P9b")
    assert p9["metrics"] == {"rostered": 4, "covered": 3, "rows": 4, "null_cap_hit": 1}
    assert p9["status"] == "fail"  # 75 % < the 90 % threshold
    assert p9b["status"] == "fail" and "50.0%" in p9b["summary"]


def test_a_tier_restricted_endpoint_is_a_finding_not_a_crash(db, world, tmp_path):
    upstream = MiniUpstream(
        {"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route(), "/nba/v1/standings": 401}
    )
    result = run(upstream, db, tmp_path, only={"P1-P2", "P4"}, tier="goat")
    assert finding(result, "P4")["status"] == "restricted"
    assert "inferred plan restriction" in finding(result, "P4")["summary"]


def test_steps_outside_the_declared_tier_are_skipped_without_a_request(db, world, tmp_path):
    upstream = MiniUpstream({})
    result = run(upstream, db, tmp_path, only={"P9"}, tier="free")
    assert finding(result, "P9")["status"] == "restricted"
    assert upstream.requests == []


# ------------------------------------------------------- budget, caching, hygiene


def test_a_probe_that_would_exceed_the_budget_is_refused_before_it_spends(db, world, tmp_path):
    upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route()})
    result = run(upstream, db, tmp_path, only={"P1-P2", "P9"}, budget=5)
    assert finding(result, "P9")["status"] == "error"
    assert "budget" in finding(result, "P9")["summary"]
    assert result["requests_made"] <= 5


def test_replay_reproduces_the_findings_with_zero_requests(db, world, tmp_path):
    routes = {"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route(), "/nba/v1/season_averages/general": averages_route(1.0)}
    live = run(MiniUpstream(routes), db, tmp_path, only={"P1-P2", "P5"})
    assert live["live"] and live["requests_made"] > 0

    forbidden = MiniUpstream({})
    offline_provider = provider_for(forbidden)
    replayed = ev.run_plan(
        offline_provider, db, tmp_path / "eval", budget=0, only={"P1-P2", "P5"}, replay_only=True
    )
    assert forbidden.requests == [] and replayed["requests_made"] == 0
    assert not replayed["live"]
    assert finding(replayed, "P5")["metrics"]["compared"] == finding(live, "P5")["metrics"]["compared"]


def test_replay_of_something_never_recorded_says_so(db, world, tmp_path):
    upstream = MiniUpstream({})
    result = run(upstream, db, tmp_path, only={"P4"}, replay=True)
    assert finding(result, "P4")["status"] == "error"
    assert "not recorded" in finding(result, "P4")["summary"]


def test_the_api_key_is_never_written_to_the_evidence_directory(db, world, tmp_path):
    upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route()})
    run(upstream, db, tmp_path, only={"P1-P2"})
    contents = "".join(
        p.read_text() for p in (tmp_path / "eval").rglob("*") if p.is_file()
    )
    assert contents and KEY not in contents


def test_the_report_states_that_pass_means_agreement_not_truth(db, world, tmp_path):
    upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route()})
    run(upstream, db, tmp_path, only={"P1-P2"})
    text = Path(tmp_path / "eval" / "report.md").read_text()
    assert "not independent truth" in text and "| P1 |" in text


def test_sample_selection_is_deterministic_and_drawn_from_pivots_data(db, world):
    first = ev.select_sample_players(db, "2025-26")
    again = ev.select_sample_players(db, "2025-26")
    assert first == again
    assert set(first["starters"]) <= {1001, 1002, 1003, 1004}
    assert set(first) == {"starters", "reserves", "rookies", "moved_teams"}


def test_an_invalid_key_probe_reports_unauthorized(db, world, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") == KEY:
            return httpx.Response(200, json={"data": [ATL, BOS]})
        return httpx.Response(401, json={"error": "bad key"})

    provider = BalldontlieProvider(
        BalldontlieClient(KEY, tier="goat", max_retries=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    )
    ctx = ev.Context(provider, db, ev.Season.parse("2025-26"), tmp_path, 50)
    # The error probe builds its own client with a wrong key against the same base URL; with
    # a mock transport it cannot reach the mock, so assert the classification path directly.
    bad = BalldontlieProvider(
        BalldontlieClient("wrong", max_retries=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    )
    assert bad.teams().status == ProviderStatus.UNAUTHORIZED
    assert ctx.left() == 50
