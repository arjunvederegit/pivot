"""R8 acceptance: Team Overview, Player Evaluation and Strategy Lab agree about the same facts.

The workflow is Team Overview (the roster page) -> Player Evaluation (a player's page and the
directory) -> Strategy Lab / the trade evaluator (`EvaluationService`). Before R8 each of the
three rebuilt "who is on this team, and what did they do" from its own copy of the query. The
tests below assert that for every team, every rostered player and every statistic they share,
the three consumers return the same identity, the same season, the same figures and the same
provenance — and that the new transparency fields tell the truth about age and gaps.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_db
from app.db.models import PlayerSeasonStats, RosterEntry
from app.services.evaluation import EvaluationService
from tests.conftest import make_player, make_team


@pytest.fixture()
def client(db, cap_params):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def league(seeded_league):
    return seeded_league


def _team_ids(league) -> list[str]:
    return [league["team_a"].id, league["team_b"].id]


class TestThreeToolsAgreeOnMembership:
    def test_team_overview_player_directory_and_strategy_lab_list_the_same_people(
        self, client, db, league
    ):
        for team_id in _team_ids(league):
            overview = client.get(f"/api/v1/teams/{team_id}/roster").json()
            overview_ids = {p["player_id"] for p in overview["roster"]}

            directory = client.get(
                "/api/v1/players", params={"team_id": team_id, "limit": 200, "active_only": False}
            ).json()
            directory_ids = {p["id"] for p in directory["players"]}

            lab_ids = {c.player_id for c in EvaluationService(db)._roster_cards(team_id)}

            assert overview_ids == directory_ids == lab_ids
            assert len(overview_ids) == 15

    def test_every_player_page_names_the_team_the_overview_lists_them_on(self, client, league):
        for team_id in _team_ids(league):
            roster = client.get(f"/api/v1/teams/{team_id}/roster").json()
            for member in roster["roster"]:
                page = client.get(f"/api/v1/players/{member['player_id']}").json()
                assert page["player"]["current_team"]["id"] == team_id
                assert page["membership_status"] == "member"
                assert page["data_status"]["roster"]["season"] == roster["season"]

    def test_the_trade_evaluator_reads_the_same_roster_as_the_overview(self, client, db, league):
        """Strategy Lab's all-rosters query and the per-team query return the same sets."""
        service = EvaluationService(db)
        grouped = service._all_rosters()
        for team_id in _team_ids(league):
            assert {c.player_id for c in grouped[team_id]} == {
                p["player_id"] for p in client.get(f"/api/v1/teams/{team_id}/roster").json()["roster"]
            }


class TestStatisticsAreTheSameNumbersEverywhere:
    def test_team_page_and_player_page_report_identical_season_figures(self, client, league):
        team_id = league["team_a"].id
        team_stats = client.get(f"/api/v1/teams/{team_id}/season-stats").json()
        assert team_stats["stats"], "fixture should have stats"
        for player_id, row in team_stats["stats"].items():
            history = client.get(f"/api/v1/players/{player_id}/stats").json()
            current = next(s for s in history["seasons"] if s["season"] == team_stats["season"])
            assert current["base"]["GP"] == row["GP"]
            assert current["base"]["PTS"] == row["PTS"]
            assert current["base"]["MIN"] == row["MIN"]

    def test_both_declare_the_same_definition_of_those_figures(self, client, league):
        team_id = league["team_a"].id
        team_stats = client.get(f"/api/v1/teams/{team_id}/season-stats").json()
        player_id = next(iter(team_stats["stats"]))
        history = client.get(f"/api/v1/players/{player_id}/stats").json()
        definition = team_stats["stat_definition"]
        assert definition["aggregation"] == "per_game"
        assert definition["season_type"] == history["season_type"] == "regular"
        assert history["stat_definitions"]["base"]["aggregation"] == definition["aggregation"]
        assert history["stat_definitions"]["base"]["minutes_unit"] == definition["minutes_unit"]

    def test_a_playoff_row_changes_neither_page(self, client, db, league):
        team_id = league["team_a"].id
        player = league["roster_a"][0]
        before_team = client.get(f"/api/v1/teams/{team_id}/season-stats").json()["stats"][player.id]
        before_player = client.get(f"/api/v1/players/{player.id}/stats").json()
        db.add(
            PlayerSeasonStats(
                player_id=player.id,
                season="2025-26",
                season_type="playoffs",
                stat_type="base",
                games_played=4,
                minutes=40.0,
                stats={"PTS": 99.0, "REB": 99.0, "AST": 99.0},
                source_provider="nba_api",
                source_retrieved_at=datetime.now(UTC),
            )
        )
        db.commit()
        after_team = client.get(f"/api/v1/teams/{team_id}/season-stats").json()["stats"][player.id]
        after_player = client.get(f"/api/v1/players/{player.id}/stats").json()
        assert after_team == before_team
        assert after_player["seasons"] == before_player["seasons"]

    def test_a_rostered_player_without_stats_is_listed_as_missing_not_zero(self, client, db, league):
        team_id = league["team_a"].id
        rookie = make_player(db, 5555, "Fixture Rookie", league["team_a"])
        payload = client.get(f"/api/v1/teams/{team_id}/season-stats").json()
        assert rookie.id not in payload["stats"]
        assert rookie.id in payload["missing_player_ids"]
        status = payload["data_status"]["player_stats"]
        assert status["completeness"] == round(15 / 16, 4)
        assert any("absent, not zero" in note for note in status["notes"])

    def test_stats_for_another_season_are_not_the_current_teams_performance(
        self, client, db, league
    ):
        """A player's 2024-25 row must not appear as 2025-26 production."""
        team_id = league["team_a"].id
        player = make_player(db, 6666, "Fixture Arrival", league["team_a"])
        db.add(
            PlayerSeasonStats(
                player_id=player.id,
                season="2024-25",
                stat_type="base",
                games_played=70,
                minutes=33.0,
                stats={"PTS": 30.0},
                source_provider="nba_api",
                source_retrieved_at=datetime.now(UTC),
            )
        )
        db.commit()
        payload = client.get(f"/api/v1/teams/{team_id}/season-stats").json()
        assert player.id not in payload["stats"] and player.id in payload["missing_player_ids"]
        history = client.get(f"/api/v1/players/{player.id}/stats").json()
        assert [s["season"] for s in history["seasons"]] == ["2024-25"]


class TestSeasonTotalsAttributeProductionToTheRightTeam:
    def test_a_traded_players_totals_name_both_the_old_and_the_current_team(
        self, client, db, league
    ):
        player = league["roster_a"][1]
        db.add(
            PlayerSeasonStats(
                player_id=player.id,
                team_id=league["team_b"].id,  # accumulated with BBB...
                season="2025-26",
                stat_type="totals",
                games_played=40,
                minutes=1200.0,
                stats={"GP": 40, "PTS": 800, "per_game": {"PTS": 20.0}, "rates": {}},
                source_provider="user_import_csv",
                source_retrieved_at=datetime.now(UTC),
            )
        )
        db.commit()
        body = client.get("/api/v1/players/season-totals/2025-26").json()
        row = next(p for p in body["players"] if p["player_id"] == player.id)
        assert row["team_abbr"] == "AAA", "current roster team"
        assert row["season_team_abbr"] == "BBB", "the team the totals were accumulated with"
        assert body["season_type"] == "regular"
        assert body["data_status"]["player_stats"]["present_count"] == 1


class TestProvenanceAgrees:
    def test_the_roster_payload_and_its_data_status_describe_the_same_data(self, client, league):
        roster = client.get(f"/api/v1/teams/{league['team_a'].id}/roster").json()
        status = roster["data_status"]["roster"]
        assert status["providers"] == roster["source_providers"] == ["nba_api"]
        assert status["season"] == roster["season"]
        assert status["present_count"] == len(roster["roster"])
        assert status["freshness"] == "fresh"
        assert any("observed snapshot" in n for n in status["notes"])

    def test_a_stale_snapshot_says_so_on_every_tool_that_reads_it(self, client, db, league):
        old = datetime.now(UTC) - timedelta(days=89)
        for entry in db.query(RosterEntry).all():
            entry.source_retrieved_at = old
        db.commit()
        team_id = league["team_a"].id
        roster = client.get(f"/api/v1/teams/{team_id}/roster").json()
        stats = client.get(f"/api/v1/teams/{team_id}/season-stats").json()
        page = client.get(f"/api/v1/players/{league['roster_a'][0].id}").json()
        for status in (
            roster["data_status"]["roster"],
            stats["data_status"]["roster"],
            page["data_status"]["roster"],
        ):
            assert status["freshness"] == "stale"
            assert status["age_seconds"] >= 89 * 86_400
        freshness = client.get("/api/v1/data/freshness").json()
        assert "rosters" in freshness["attention"]

    def test_a_demo_row_is_never_described_as_nba_data_in_the_new_fields(self, client, db, league):
        for entry in db.query(RosterEntry).filter_by(team_id=league["team_a"].id):
            entry.source_provider = "demo_seed"
        db.commit()
        roster = client.get(f"/api/v1/teams/{league['team_a'].id}/roster").json()
        status = roster["data_status"]["roster"]
        assert status["providers"] == ["demo_seed"]
        assert "NBA.com" not in status["upstream"]

    def test_an_empty_database_reports_missing_not_fresh(self, client, db):
        body = client.get("/api/v1/data/freshness").json()
        assert body["datasets"]["rosters"]["freshness"] == "missing"
        assert set(body["attention"]) >= {"rosters", "standings", "player_stats"}

    def test_the_provider_registry_never_exposes_a_credential(self, client, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("BALLDONTLIE_API_KEY", "not-a-real-key-cccccccc")
        get_settings.cache_clear()
        try:
            body = client.get("/api/v1/data/providers").json()
        finally:
            get_settings.cache_clear()
        text = str(body)
        assert "not-a-real-key" not in text and "cccccccc" not in text
        bdl = next(p for p in body["providers"] if p["provider"] == "balldontlie")
        assert bdl["status"] == "configured" and bdl["live_verified"] is False


class TestIdentityIsOneThing:
    def test_a_namesake_pair_resolves_to_distinct_pages(self, client, db):
        team = make_team(db, 1, "AAA")
        current = make_player(db, 1630314, "Brandon Williams", team)
        retired = make_player(db, 1585, "Brandon Williams", None)
        a = client.get(f"/api/v1/players/{current.id}").json()["player"]
        b = client.get(f"/api/v1/players/{retired.id}").json()["player"]
        assert a["nba_player_id"] == 1630314 and a["current_team"]["id"] == team.id
        assert b["nba_player_id"] == 1585 and b["current_team"] is None
        assert client.get(f"/api/v1/players/{retired.id}").json()["membership_status"] == "unlisted"


# ---------------------------------------------------------------------------- R8.1: the rollover


class TestToolsStayConsistentAcrossTheSeasonRollover:
    """The same checks, on both sides of the day the tools move from 2025-26 to 2026-27.

    R8 showed the tools agreed on one snapshot. That says nothing about the day the season key
    changes, which is when hand-copied definitions drift. A player is traded, a rookie arrives with
    no history, and 2026-27 statistics do not exist yet; all three tools must still tell one story.
    """

    @pytest.fixture()
    def rolled(self, db, league, monkeypatch):
        from app.config import get_settings

        team_a, team_b = league["team_a"], league["team_b"]
        traded = league["roster_a"][0]
        stayer = league["roster_a"][1]
        rookie = make_player(db, 9001, "Rookie Arrival", team_a, season="2026-27")
        db.add_all(
            [
                RosterEntry(team_id=team_b.id, player_id=traded.id, season="2026-27",
                            is_current=True, source_retrieved_at=datetime.now(UTC)),
                RosterEntry(team_id=team_a.id, player_id=stayer.id, season="2026-27",
                            is_current=True, source_retrieved_at=datetime.now(UTC)),
            ]
        )
        db.commit()
        # Deterministic clock: this test is about 18 Oct 2026, whenever it runs.
        monkeypatch.setattr("app.canonical.provenance.today_in_league", lambda now=None: date(2026, 10, 18))
        monkeypatch.setenv("CURRENT_SEASON", "2026-27")
        get_settings.cache_clear()
        yield {"traded": traded, "stayer": stayer, "rookie": rookie, **league}
        get_settings.cache_clear()

    def test_after_the_flip_all_three_tools_list_the_new_season_roster(self, client, db, rolled):
        for team_id in (rolled["team_a"].id, rolled["team_b"].id):
            overview = client.get(f"/api/v1/teams/{team_id}/roster").json()
            assert overview["season"] == "2026-27"
            ids = {p["player_id"] for p in overview["roster"]}
            directory = client.get(
                "/api/v1/players", params={"team_id": team_id, "limit": 200, "active_only": False}
            ).json()
            assert ids == {p["id"] for p in directory["players"]}
            assert ids == {c.player_id for c in EvaluationService(db)._roster_cards(team_id)}

    def test_the_traded_player_is_on_one_team_everywhere_and_keeps_last_seasons_line(
        self, client, rolled
    ):
        traded = rolled["traded"]
        page = client.get(f"/api/v1/players/{traded.id}").json()
        assert page["player"]["current_team"]["id"] == rolled["team_b"].id
        assert page["membership_status"] == "member"
        team_b_ids = {p["player_id"] for p in client.get(
            f"/api/v1/teams/{rolled['team_b'].id}/roster").json()["roster"]}
        team_a_ids = {p["player_id"] for p in client.get(
            f"/api/v1/teams/{rolled['team_a'].id}/roster").json()["roster"]}
        assert traded.id in team_b_ids and traded.id not in team_a_ids
        history = client.get(f"/api/v1/players/{traded.id}/stats").json()
        assert any(s["season"] == "2025-26" for s in history["seasons"]), "last season's record survives"

    def test_nobody_gets_a_zero_for_a_season_that_has_not_been_played(self, client, rolled):
        body = client.get(f"/api/v1/teams/{rolled['team_a'].id}/season-stats").json()
        assert body["season"] == "2026-27"
        assert body["stats"] == {}, "no 2026-27 rows exist; none are invented or carried over"
        assert set(body["missing_player_ids"]) == {
            p["player_id"] for p in client.get(
                f"/api/v1/teams/{rolled['team_a'].id}/roster").json()["roster"]}
        status = body["data_status"]["player_stats"]
        assert status["freshness"] == "not_yet_available"
        assert status["scope_state"] == "upcoming"
        assert body["data_status"]["roster"]["freshness"] == "fresh", "the roster itself is current"

    def test_the_rookie_is_a_member_with_no_history(self, client, rolled):
        rookie = rolled["rookie"]
        page = client.get(f"/api/v1/players/{rookie.id}").json()
        assert page["membership_status"] == "member"
        history = client.get(f"/api/v1/players/{rookie.id}/stats").json()
        assert history["seasons"] == [] or all(s["season"] != "2026-27" for s in history["seasons"])

    def test_before_the_stats_flip_the_roster_is_already_2026_27_and_the_statistics_are_not(
        self, client, db, league, monkeypatch
    ):
        """R8.2 (replaces R8.1's "nothing leaks"): rosters follow the league year, statistics the rollover.

        On 18 Oct 2026 the tools read 2025-26 *statistics* (the rollover is 3 Nov) but the 2026-27
        *roster*. A player staged on a new team for 2026-27 is on that team, and the statistics
        beside him are still named for the season they come from.
        """
        from app.config import get_settings

        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: date(2026, 10, 18))
        mover = league["roster_a"][0]
        db.add(RosterEntry(team_id=league["team_b"].id, player_id=mover.id,
                           season="2026-27", is_current=True, source_retrieved_at=datetime.now(UTC)))
        db.commit()
        get_settings.cache_clear()
        try:
            a = client.get(f"/api/v1/teams/{league['team_a'].id}/roster").json()
            b = client.get(f"/api/v1/teams/{league['team_b'].id}/roster").json()
            assert a["season"] == "2025-26", "the statistics season has not rolled over"
            assert a["roster_season"] == b["roster_season"] == "2026-27"
            assert mover.id not in {p["player_id"] for p in a["roster"]}
            assert mover.id in {p["player_id"] for p in b["roster"]}
            context = b["season_context"]
            assert context["roster"]["season"] == "2026-27"
            assert context["performance"]["season"] == "2025-26"
            assert context["performance"]["is_baseline"] is True
            assert context["roster"]["provisional"] is True
        finally:
            get_settings.cache_clear()
