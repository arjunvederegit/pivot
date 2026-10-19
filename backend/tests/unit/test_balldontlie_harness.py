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
from app.canonical.seasons import Season
from app.db.models import Contract, ContractYear, PlayerSeasonStats, Standing
from app.integrations.balldontlie import evaluation as ev
from app.integrations.balldontlie.client import BalldontlieClient
from app.integrations.balldontlie.provider import BalldontlieProvider
from tests.conftest import make_player, make_team

KEY = "not-a-real-key-aaaaaaaa"  # fixture value; low entropy on purpose (secret scanners)

ATL = {"id": 1, "abbreviation": "ATL", "full_name": "Atlanta Hawks", "city": "Atlanta", "name": "Hawks",
       "conference": "East", "division": "Southeast"}
BOS = {"id": 2, "abbreviation": "BOS", "full_name": "Boston Celtics", "city": "Boston", "name": "Celtics",
       "conference": "East", "division": "Atlantic"}


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
        # The fixtures are 2025-26 data; the default (last completed season) would change under them
        # when the calendar crosses 2027-04-11.
        season=Season.parse("2025-26"),
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
    assert p9["metrics"] == {
        "rostered": 4, "covered": 3, "rows": 4, "null_cap_hit": 1,
        "roster_season": "2025-26",  # no 2026-27 rosters are staged in this fixture, and it says so
    }
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


# ------------------------------------------------------------------ R8.1: trial readiness


class TestComparisonSeasonAndRosterScoping:
    def test_the_default_comparison_season_is_the_last_completed_one_on_both_sides_of_the_rollover(
        self, db, world, tmp_path, monkeypatch
    ):
        from datetime import date

        upstream = MiniUpstream({"/nba/v1/teams": teams_route()})
        for today, expected in ((date(2026, 10, 18), "2025-26"), (date(2026, 11, 20), "2025-26"),
                                (date(2027, 5, 1), "2026-27")):
            monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None, d=today: d)
            result = ev.run_plan(provider_for(upstream), db, tmp_path / str(today),
                                 only={"P1-P2"}, budget=50)
            assert result["season"] == expected, today

    def test_sampling_reads_the_roster_of_the_season_it_names_not_every_current_row(self, db, world):
        from app.db.models import RosterEntry

        bos, ann = world["bos"], world["a"]
        db.add(RosterEntry(team_id=bos.id, player_id=ann.id, season="2026-27", is_current=True,
                           source_retrieved_at=datetime.now(UTC)))
        db.commit()
        # Ann's 2025-26 stat row is ATL; her staged 2026-27 roster is BOS => "moved".
        assert 1001 in ev.select_sample_players(db, "2025-26", "2026-27")["moved_teams"]
        # Read against 2025-26 rosters she is where she was. The unscoped query (every is_current
        # row) would have put her on both teams and listed her as moved either way.
        assert 1001 not in ev.select_sample_players(db, "2025-26", "2025-26")["moved_teams"]

    def test_rookies_are_drawn_from_the_roster_season_not_from_every_current_row(self, db, world):
        from app.db.models import RosterEntry

        old_rookie = make_player(db, 2001, "Last Year Rookie", world["atl"], season="2025-26")
        old_rookie.years_experience = 0
        new_rookie = make_player(db, 2002, "This Year Rookie", world["bos"], season="2026-27")
        new_rookie.years_experience = 0
        db.commit()
        assert db.query(RosterEntry).filter_by(season="2025-26", is_current=True).count() >= 1
        sample = ev.select_sample_players(db, "2025-26", "2026-27")
        assert sample["rookies"] == [2002], "a 2025-26-only rookie is not on the 2026-27 roster"

    def test_the_roster_season_is_the_league_season_only_once_it_is_staged(self, db, world, monkeypatch):
        from datetime import date

        from app.db.models import RosterEntry

        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: date(2026, 10, 18))
        assert ev.pick_roster_season(db, Season.parse("2025-26")).label == "2025-26"
        db.add(RosterEntry(team_id=world["bos"].id, player_id=world["a"].id, season="2026-27",
                           is_current=True, source_retrieved_at=datetime.now(UTC)))
        db.commit()
        assert ev.pick_roster_season(db, Season.parse("2025-26")).label == "2026-27"


class TestPreflight:
    @pytest.fixture()
    def ready_db(self, db, world):
        from app.db.models import Game

        now = datetime.now(UTC)
        db.query(PlayerSeasonStats).update({"source_retrieved_at": now})
        db.query(Standing).update({"source_retrieved_at": now})
        db.add(Game(nba_game_id="g1", season="2025-26", game_date=datetime(2025, 10, 21).date(),
                    source_retrieved_at=now))
        db.commit()
        return db

    def _provider(self, key=KEY):
        return BalldontlieProvider(BalldontlieClient(key, tier="trial", transport=httpx.MockTransport(lambda r: httpx.Response(500)),
                                                     sleep=lambda s: None))

    def test_a_missing_key_is_a_blocker_but_replay_needs_none(self, ready_db, monkeypatch):
        from datetime import date

        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: date(2026, 10, 18))
        gate = ev.preflight(ready_db, self._provider(""), season=Season.parse("2025-26"))
        assert gate["ready"] is False and any("BALLDONTLIE_API_KEY" in b for b in gate["blockers"])

    def test_local_data_that_is_missing_blocks_with_the_command_that_fixes_it(self, db, world, monkeypatch):
        from datetime import date

        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: date(2026, 10, 18))
        gate = ev.preflight(db, self._provider(), season=Season.parse("2025-26"))
        assert gate["ready"] is False
        assert any("games" in b and "refresh" in b for b in gate["blockers"])

    def test_a_finalized_comparison_season_passes_and_the_warnings_are_specific(self, ready_db, monkeypatch):
        from datetime import date

        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: date(2026, 10, 18))
        gate = ev.preflight(ready_db, self._provider(), season=Season.parse("2025-26"))
        assert gate["blockers"] == [] and gate["ready"] is True
        assert gate["local_data"]["player_stats"]["freshness"] == "finalized"
        assert any("no 2026-27 rosters are staged" in w for w in gate["warnings"])
        assert any("no contract snapshot" in w for w in gate["warnings"])
        assert any("never been called live" in w and "link-balldontlie" in w for w in gate["warnings"])
        assert gate["estimated_requests"] == ev.plan_summary("trial")["estimated_requests"]

    def test_a_budget_smaller_than_the_plan_is_a_blocker(self, ready_db, monkeypatch):
        from datetime import date

        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: date(2026, 10, 18))
        gate = ev.preflight(ready_db, self._provider(), season=Season.parse("2025-26"), budget=20)
        assert any("budget is 20" in b for b in gate["blockers"])

    def test_preflight_makes_no_request(self, ready_db, monkeypatch):
        from datetime import date

        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: date(2026, 10, 18))
        provider = self._provider()
        ev.preflight(ready_db, provider, season=Season.parse("2025-26"))
        assert provider.client.request_count == 0


class TestTheReportSaysWhatDidNotRun:
    def test_excluded_and_out_of_plan_probes_are_listed_not_silently_omitted(self, db, world, tmp_path):
        upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route()})
        result = run(upstream, db, tmp_path, only={"P1-P2"}, tier="free")
        statuses = {f["probe"]: f["status"] for f in result["findings"]}
        assert statuses["P3"] == "not_run" and statuses["P13"] == "not_run"
        assert result["status_counts"]["not_run"] >= 8
        report = (tmp_path / "eval" / "report.md").read_text()
        assert "not_run" in report and "compared on 2025-26 statistics" in report

    def test_a_tier_that_excludes_a_probe_marks_it_restricted(self, db, world, tmp_path):
        upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route()})
        result = run(upstream, db, tmp_path, tier="free", budget=300)
        restricted = {f["probe"] for f in result["findings"] if f["status"] == "restricted"}
        assert {"P5", "P9", "P11"} <= restricted

    def test_a_truncated_read_makes_the_probe_incomplete_not_a_pass(self, db, world, tmp_path):
        pages = {"n": 0}

        def games(request):
            pages["n"] += 1
            return {"data": [], "meta": {"next_cursor": pages["n"]}}

        upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route(),
                                 "/nba/v1/games": games})
        provider = provider_for(upstream)
        result = ev.run_plan(provider, db, tmp_path / "t", only={"P1-P2", "P3"}, budget=200,
                             season=Season.parse("2025-26"))
        incomplete = [f for f in result["findings"] if f["status"] == "inconclusive" and "incomplete read" in f["summary"]]
        assert incomplete, "a capped pagination must surface as an incomplete finding"


class TestRosterAndTeamAgreementProbes:
    """R8.1: the trial must be able to say whether BALLDONTLIE agrees on *rosters* and *team* figures."""

    def _active(self, moved_to_bos: tuple[int, ...] = ()):
        def route(request):
            rows = [
                bdl_player(11, "Ann", "Alpha", BOS if 11 in moved_to_bos else ATL),
                bdl_player(12, "Ben", "Bravo", BOS if 12 in moved_to_bos else ATL),
                bdl_player(13, "Cal", "Charlie", BOS),
                bdl_player(14, "Dee", "Delta", BOS),
            ]
            return {"data": rows, "meta": {}}

        return route

    def test_p2b_passes_when_both_sources_put_players_on_the_same_teams(self, db, world, tmp_path):
        upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": self._active()})
        p2b = finding(run(upstream, db, tmp_path, only={"P1-P2"}), "P2b")
        assert p2b["status"] == "pass" and p2b["requests"] == 0
        assert p2b["metrics"]["compared"] == 4 and p2b["metrics"]["agree"] == 4

    def test_p2b_fails_and_names_who_disagrees(self, db, world, tmp_path):
        upstream = MiniUpstream({"/nba/v1/teams": teams_route(),
                                 "/nba/v1/players/active": self._active(moved_to_bos=(11, 12))})
        p2b = finding(run(upstream, db, tmp_path, only={"P1-P2"}), "P2b")
        assert p2b["status"] == "fail"
        assert p2b["metrics"]["agree"] == 2
        assert {e["player"] for e in p2b["metrics"]["disagreement_examples"]} == {"Ann Alpha", "Ben Bravo"}

    def test_p5b_compares_team_points_per_game(self, db, world, tmp_path):
        from app.db.models import TeamSeasonStats

        for team, pts in ((world["atl"], 112.4), (world["bos"], 118.9)):
            db.add(TeamSeasonStats(team_id=team.id, season="2025-26", season_type="regular",
                                   stat_type="base", stats={"PTS": pts, "GP": 82}))
        db.commit()
        rows = [{"team": ATL, "season": 2025, "season_type": "regular", "stats": {"pts": 112.4}},
                {"team": BOS, "season": 2025, "season_type": "regular", "stats": {"pts": 99.0}}]  # BOS wrong
        upstream = MiniUpstream({"/nba/v1/teams": teams_route(), "/nba/v1/players/active": active_players_route(),
                                 "/nba/v1/team_season_averages/general": lambda r: {"data": rows, "meta": {}}})
        result = run(upstream, db, tmp_path, only={"P1-P2", "P5b"})
        p5b = finding(result, "P5b")
        assert p5b["metrics"]["compared"] == 2 and p5b["metrics"]["agree"] == 1
        assert p5b["status"] == "fail"  # 50 % < 95 %

    def test_the_plan_still_fits_the_trial_budget_and_names_both_probes(self):
        plan = ev.plan_summary("trial")
        assert "P5b" in {s["id"] for s in plan["steps"]}
        assert plan["estimated_requests"] < 200
