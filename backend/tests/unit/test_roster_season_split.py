"""R8.2 — the roster season and the statistics season are two clocks.

Before R8.2 one string (`Settings.current_season`) governed both, so in the summer and preseason the
tools showed last season's rosters because last season is where the statistics are. These tests pin
the separation: rosters follow the league year (2026-27 from 1 July 2026), statistics follow the
rollover (3 Nov 2026), contracts follow `CAP_LEAGUE_YEAR`, and no one of them can fabricate or
force another.

Every date is injected; none of these tests depends on the day the suite runs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.calendar import season_context
from app.canonical.rosters import (
    choose_roster_season,
    current_roster,
    current_team_of,
    resolve_roster_season,
    roster_as_of,
)
from app.canonical.tool_seasons import tool_seasons
from app.config import get_settings
from app.db.base import get_db
from app.db.models import Contract, ContractYear, PlayerSeasonStats, RosterEntry
from app.services.evaluation import EvaluationService
from tests.conftest import make_player

OPENING_NIGHT = date(2026, 10, 20)


@pytest.fixture()
def client(db, cap_params):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def on(monkeypatch, request):
    """Pin the clock: ``on(date(2026, 10, 18))``. Settings are re-read when the clock moves."""

    def _set(day: date) -> None:
        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: day)
        get_settings.cache_clear()

    request.addfinalizer(get_settings.cache_clear)
    return _set


@pytest.fixture()
def staged(db: Session, seeded_league: dict) -> dict:
    """2025-26 rosters (the fixture's) plus a staged 2026-27 snapshot with every case that matters.

    * ``stayer``  — A in 2025-26, A in 2026-27 (returning player)
    * ``traded``  — A in 2025-26, B in 2026-27; his 2025-26 line is with A
    * ``rookie``  — on A in 2026-27 only, ``years_experience = 0``, no statistics anywhere
    """
    league = seeded_league
    team_a, team_b = league["team_a"], league["team_b"]
    now = datetime.now(UTC)
    traded, stayer = league["roster_a"][0], league["roster_a"][1]
    rookie = make_player(db, 9001, "Rookie Arrival", None)
    rookie.years_experience = 0
    entries = []
    for team, players in ((team_a, league["roster_a"]), (team_b, league["roster_b"])):
        for player in players:
            if player.id == traded.id:
                continue
            entries.append((team.id, player.id))
    entries.append((team_b.id, traded.id))
    entries.append((team_a.id, rookie.id))
    for team_id, player_id in entries:
        db.add(
            RosterEntry(
                team_id=team_id, player_id=player_id, season="2026-27", is_current=True,
                age=26.0, source_retrieved_at=now, source_provider="nba_api",
            )
        )
    db.commit()
    return {**league, "traded": traded, "stayer": stayer, "rookie": rookie}


# ------------------------------------------------------------------ the calendar's two clocks


class TestTheTwoClocks:
    @pytest.mark.parametrize(
        ("day", "performance", "baseline"),
        [
            (date(2026, 10, 17), "2025-26", True),  # before opening night
            (OPENING_NIGHT, "2025-26", True),  # opening night
            (date(2026, 10, 22), "2025-26", True),  # the first games
            (date(2026, 11, 2), "2025-26", True),  # the day before the statistics rollover
            (date(2026, 11, 3), "2026-27", False),  # the rollover
            (date(2026, 11, 20), "2026-27", False),  # after it
        ],
    )
    def test_the_roster_season_never_waits_for_the_statistics_but_the_statistics_keep_their_own_clock(
        self, day, performance, baseline
    ):
        ctx = season_context(day)
        assert ctx.roster_season.label == "2026-27", "membership is the league year, on every date"
        assert ctx.performance_season.label == performance
        assert ctx.analysis_is_baseline is baseline
        assert ctx.projection_target_season.label == "2026-27"

    def test_the_league_year_turns_over_on_1_july_and_the_statistics_do_not(self):
        june, july = season_context(date(2026, 6, 30)), season_context(date(2026, 7, 1))
        assert (june.roster_season.label, july.roster_season.label) == ("2025-26", "2026-27")
        assert july.performance_season.label == "2025-26"

    def test_the_roster_basis_is_labelled_provisional_until_the_regular_season(self):
        assert season_context(date(2026, 7, 15)).roster_basis == "offseason_snapshot"
        assert season_context(date(2026, 10, 18)).roster_basis == "provisional_preseason"
        assert season_context(date(2026, 10, 21)).roster_basis == "regular_season_snapshot"


class TestConfigurationOverrides:
    def test_a_stale_statistics_pin_does_not_drag_rosters_back(self, monkeypatch, on):
        """The old `.env.example` pinned CURRENT_SEASON=2025-26; that must not pin rosters."""
        monkeypatch.setenv("CURRENT_SEASON", "2025-26")
        on(date(2026, 11, 20))  # after the rollover: the pin is now behind the calendar
        settings = get_settings()
        assert settings.current_season == "2025-26", "the pin still governs statistics"
        assert settings.roster_season_preferred == "2026-27"

    def test_roster_season_has_its_own_explicit_override(self, monkeypatch, on):
        monkeypatch.setenv("ROSTER_SEASON", "2024-25")
        on(date(2026, 10, 18))
        assert get_settings().roster_season_preferred == "2024-25"

    def test_a_malformed_override_fails_loudly(self, monkeypatch, on):
        monkeypatch.setenv("ROSTER_SEASON", "26-27")
        on(date(2026, 10, 18))
        with pytest.raises(ValueError):
            _ = get_settings().roster_season_preferred


# ---------------------------------------------------------- reading rosters out of the database


class TestRosterLookup:
    def test_the_default_roster_is_the_league_years_not_the_statistics_seasons(
        self, db, staged, on
    ):
        on(date(2026, 10, 18))
        assert resolve_roster_season(db) == "2026-27"
        snap = current_roster(db, staged["team_b"].id)
        assert snap.season == "2026-27"
        assert staged["traded"].id in snap.player_ids

    def test_an_explicit_historical_season_still_returns_that_seasons_roster(self, db, staged, on):
        on(date(2026, 10, 18))
        old = current_roster(db, staged["team_a"].id, "2025-26")
        assert old.season == "2025-26"
        assert staged["traded"].id in old.player_ids
        assert staged["rookie"].id not in old.player_ids
        assert current_team_of(db, staged["traded"].id, "2025-26").team.id == staged["team_a"].id
        assert current_team_of(db, staged["traded"].id).team.id == staged["team_b"].id

    def test_roster_as_of_keeps_working_for_a_historical_season(self, db, staged, on):
        on(date(2026, 10, 18))
        for entry in db.scalars(select(RosterEntry).where(RosterEntry.season == "2025-26")).all():
            entry.valid_from = datetime(2026, 4, 1, tzinfo=UTC)
        db.commit()
        later = datetime(2026, 6, 1, tzinfo=UTC)
        assert staged["traded"].id in roster_as_of(db, later, staged["team_a"].id, "2025-26").player_ids

    def test_no_2026_27_snapshot_yet_falls_back_visibly_instead_of_showing_nothing(
        self, db, seeded_league, on
    ):
        on(date(2026, 7, 2))  # league year turned over, nothing ingested yet
        choice = choose_roster_season(db)
        assert (choice.season, choice.preferred, choice.fell_back) == ("2025-26", "2026-27", True)
        context = tool_seasons(db)
        assert context["roster"]["status"] == "fallback_previous_snapshot"
        assert context["roster"]["provisional"] is True
        assert any("no current 2026-27 roster snapshot" in n for n in context["notes"])

    def test_an_empty_database_is_not_a_fallback(self, db, on):
        on(date(2026, 10, 18))
        choice = choose_roster_season(db)
        assert (choice.season, choice.fell_back) == ("2026-27", False)

    def test_current_membership_does_not_depend_on_current_season_statistics(
        self, db, staged, on
    ):
        """No 2026-27 statistics exist anywhere in this database, and the roster is complete."""
        on(date(2026, 10, 18))
        assert (
            db.query(PlayerSeasonStats).filter(PlayerSeasonStats.season == "2026-27").count() == 0
        )
        league = current_roster(db)
        assert league.season == "2026-27" and len(league.entries) == 31

    def test_a_player_on_two_current_rosters_is_a_conflict_not_a_guess(self, db, staged, on):
        on(date(2026, 10, 18))
        db.add(
            RosterEntry(
                team_id=staged["team_a"].id, player_id=staged["traded"].id, season="2026-27",
                is_current=True, source_retrieved_at=datetime.now(UTC),
            )
        )
        db.commit()
        membership = current_team_of(db, staged["traded"].id)
        assert membership.status == "conflict" and membership.team is None
        assert current_roster(db).conflicts


# ------------------------------------------------------------------------ what the API says


class TestTeamPages:
    def test_a_traded_player_is_on_his_new_team_and_his_old_line_stays_with_the_old_team(
        self, client, staged, on
    ):
        on(date(2026, 10, 18))
        traded = staged["traded"]
        a_roster = client.get(f"/api/v1/teams/{staged['team_a'].id}/roster").json()
        b_roster = client.get(f"/api/v1/teams/{staged['team_b'].id}/roster").json()
        assert traded.id not in {p["player_id"] for p in a_roster["roster"]}
        assert traded.id in {p["player_id"] for p in b_roster["roster"]}
        assert (b_roster["season"], b_roster["roster_season"]) == ("2025-26", "2026-27")

        stats = client.get(f"/api/v1/teams/{staged['team_b'].id}/season-stats").json()
        assert stats["season"] == "2025-26" and stats["roster_season"] == "2026-27"
        line = stats["stat_attribution"][traded.id]
        assert line["stat_team_id"] == staged["team_a"].id, "the 2025-26 line was earned with A"
        assert line["stat_team_abbreviation"] == "AAA"
        assert line["with_current_team"] is False, "it is never attributed to B"
        # A player who stayed is attributed to his own team.
        a_stats = client.get(f"/api/v1/teams/{staged['team_a'].id}/season-stats").json()
        assert a_stats["stat_attribution"][staged["stayer"].id]["with_current_team"] is True

    def test_a_rookie_is_on_the_roster_with_a_missing_history_not_zeros(self, client, staged, on):
        on(date(2026, 10, 18))
        rookie = staged["rookie"]
        a_roster = client.get(f"/api/v1/teams/{staged['team_a'].id}/roster").json()
        assert rookie.id in {p["player_id"] for p in a_roster["roster"]}
        stats = client.get(f"/api/v1/teams/{staged['team_a'].id}/season-stats").json()
        assert rookie.id not in stats["stats"], "no invented line"
        assert rookie.id in stats["missing_player_ids"]
        assert stats["missing_reasons"][rookie.id] == "rookie_no_nba_history"
        page = client.get(f"/api/v1/players/{rookie.id}").json()
        assert page["membership_status"] == "member"
        assert page["roster_season"] == "2026-27"
        assert page["impact"].get("tei") is None, "no estimate is fabricated for a player with no history"
        assert client.get(f"/api/v1/players/{rookie.id}/stats").json()["seasons"] == []

    def test_a_returning_player_keeps_one_identity_and_his_history(self, client, staged, on):
        on(date(2026, 10, 18))
        stayer = staged["stayer"]
        page = client.get(f"/api/v1/players/{stayer.id}").json()
        assert page["player"]["current_team"]["id"] == staged["team_a"].id
        seasons = {s["season"] for s in client.get(f"/api/v1/players/{stayer.id}/stats").json()["seasons"]}
        assert {"2024-25", "2025-26"} <= seasons
        directory = client.get("/api/v1/players", params={"q": stayer.full_name, "active_only": False}).json()
        assert [p["id"] for p in directory["players"]].count(stayer.id) == 1

    def test_the_directory_and_the_team_page_agree_on_the_2026_27_roster(self, client, db, staged, on):
        on(date(2026, 10, 18))
        for team in (staged["team_a"], staged["team_b"]):
            page = {p["player_id"] for p in client.get(f"/api/v1/teams/{team.id}/roster").json()["roster"]}
            directory = client.get(
                "/api/v1/players", params={"team_id": team.id, "limit": 200, "active_only": False}
            ).json()
            assert page == {p["id"] for p in directory["players"]}
            assert page == {c.player_id for c in EvaluationService(db)._roster_cards(team.id)}
        assert directory["roster_season"] == "2026-27"

    def test_the_player_explorer_totals_name_both_teams(self, client, db, staged, on):
        on(date(2026, 10, 18))
        traded = staged["traded"]
        db.add(
            PlayerSeasonStats(
                player_id=traded.id, season="2025-26", stat_type="totals", games_played=70,
                minutes=2000.0, team_id=staged["team_a"].id, stats={"PTS": 900},
                source_retrieved_at=datetime.now(UTC),
            )
        )
        db.commit()
        body = client.get("/api/v1/players/season-totals/2025-26").json()
        row = next(p for p in body["players"] if p["player_id"] == traded.id)
        assert (row["team_abbr"], row["season_team_abbr"]) == ("BBB", "AAA")
        assert body["roster_season"] == "2026-27"

    def test_needs_say_which_team_they_describe(self, client, staged, on):
        on(date(2026, 10, 18))
        body = client.get(f"/api/v1/teams/{staged['team_b'].id}/needs").json()
        assert body["season"] == "2025-26" and body["roster_season"] == "2026-27"
        assert body["needs_basis"]["matches_current_roster"] is False
        assert "2025-26" in body["needs_basis"]["note"]
        assert body["computed"] is True, "they are labelled, not withheld or recomputed"

    def test_acquisition_targets_say_which_roster_they_are_scored_against(self, client, staged, on):
        on(date(2026, 10, 18))
        body = client.get(f"/api/v1/teams/{staged['team_b'].id}/acquisition-targets?limit=3").json()
        assert body["available"] is True
        assert (body["season"], body["roster_season"]) == ("2025-26", "2026-27")
        assert body["needs_match_roster"] is False

    def test_when_the_seasons_agree_no_limitation_is_claimed(self, client, seeded_league, on):
        on(date(2026, 7, 2))  # no 2026-27 snapshot: the fallback serves 2025-26 consistently
        body = client.get(f"/api/v1/teams/{seeded_league['team_a'].id}/needs").json()
        assert body["needs_basis"]["matches_current_roster"] is True
        assert body["needs_basis"]["note"] is None


# ------------------------------------------------------------------- the trade evaluator


class TestTradeEvaluation:
    @pytest.fixture(autouse=True)
    def _assets_scored(self, monkeypatch):
        """The fixture has no picks or priced payroll, so `assets` is always excluded and confidence is
        never "high" — which would hide whether the season caveat caps it. Score it."""
        monkeypatch.setattr(EvaluationService, "_assets", lambda self, *a, **k: (50.0, {}))

    def _evaluate(self, client, staged):
        a, b = staged["team_a"], staged["team_b"]
        # Two players who are on those teams in 2026-27 (the rookie is excluded: no estimate).
        a_player = staged["stayer"].id
        b_player = staged["roster_b"][2].id
        return client.post(
            "/api/v1/trades/evaluate",
            json={
                "team_ids": [a.id, b.id],
                "player_moves": [
                    {"player_id": a_player, "from_team_id": a.id, "to_team_id": b.id},
                    {"player_id": b_player, "from_team_id": b.id, "to_team_id": a.id},
                ],
                "pick_moves": [],
            },
        )

    def test_the_evaluator_scores_the_2026_27_rosters_and_labels_the_seasons(self, client, staged, on):
        on(date(2026, 10, 18))
        response = self._evaluate(client, staged)
        assert response.status_code == 200, response.text
        body = response.json()
        basis = body["season_basis"]
        assert basis["roster_season"] == "2026-27" and basis["performance_season"] == "2025-26"
        assert basis["team_needs_season"] == "2025-26"
        assert basis["roster_differs_from_performance"] is True
        assert basis["limitations"][0]["component"] == "fit"
        for evaluation in body["evaluations"].values():
            assert evaluation["season_basis"]["roster_season"] == "2026-27"
            assert evaluation["excluded_components"] == []
            assert evaluation["detail"]["fit"]["needs_season"] == "2025-26"
            assert evaluation["detail"]["fit"]["needs_match_roster"] is False
        assert body["evaluations"][staged["team_b"].id]["confidence"] == "medium", (
            "fit read needs measured on last season's team, so a clean evaluation is capped"
        )

    def test_a_player_who_is_not_on_the_2026_27_team_is_refused_not_scored(self, client, staged, on):
        """The traded player is on B now; moving him from A is a phantom move."""
        on(date(2026, 10, 18))
        a, b = staged["team_a"], staged["team_b"]
        response = client.post(
            "/api/v1/trades/evaluate",
            json={
                "team_ids": [a.id, b.id],
                "player_moves": [
                    {"player_id": staged["traded"].id, "from_team_id": a.id, "to_team_id": b.id}
                ],
                "pick_moves": [],
            },
        )
        assert response.status_code >= 400

    def test_a_player_on_no_current_roster_is_named_not_silently_scored_as_available(
        self, client, db, staged, on
    ):
        on(date(2026, 10, 18))
        a, b = staged["team_a"], staged["team_b"]
        # On a 2025-26 roster only: a free agent / waived / retired by 2026-27.
        gone = make_player(db, 9100, "Left The League", a, season="2025-26")
        db.commit()
        response = client.post(
            "/api/v1/trades/evaluate",
            json={
                "team_ids": [a.id, b.id],
                "player_moves": [{"player_id": gone.id, "from_team_id": a.id, "to_team_id": b.id}],
                "pick_moves": [],
            },
        )
        assert response.status_code == 200, response.text
        evaluations = response.json()["evaluations"]
        warnings = evaluations[b.id]["membership_warnings"]
        assert [w["name"] for w in warnings] == ["Left The League"]
        assert "no 2026-27 roster" in warnings[0]["message"]
        assert evaluations[a.id]["membership_warnings"] == [], "only arriving players are unlisted"
        assert evaluations[b.id]["confidence"] != "high"

    def test_nothing_is_flagged_when_roster_and_statistics_seasons_agree(self, client, seeded_league, on):
        on(date(2026, 7, 2))
        a, b = seeded_league["team_a"], seeded_league["team_b"]
        response = client.post(
            "/api/v1/trades/evaluate",
            json={
                "team_ids": [a.id, b.id],
                "player_moves": [
                    {"player_id": seeded_league["roster_a"][1].id, "from_team_id": a.id, "to_team_id": b.id},
                    {"player_id": seeded_league["roster_b"][2].id, "from_team_id": b.id, "to_team_id": a.id},
                ],
                "pick_moves": [],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["season_basis"]["limitations"] == []
        # BBB has no unmodelled player, so with nothing excluded its confidence is "high" — the
        # baseline the season caveat caps in the test above.
        assert body["evaluations"][b.id]["confidence"] == "high", "nothing to cap"


# ------------------------------------------------------------------------- financial honesty


class TestContractSeason:
    def test_no_2026_27_contract_data_is_missing_never_zero_and_never_last_seasons_salary(
        self, client, db, staged, on
    ):
        on(date(2026, 10, 18))
        player = staged["no_contract_a"]  # the fixture gives this player no contract at all
        contract = Contract(player_id=player.id, contract_type="standard", source_name="fixture")
        db.add(contract)
        db.flush()
        db.add(ContractYear(contract_id=contract.id, season="2025-26", salary=30_000_000))
        db.commit()
        # A 2025-26 salary is on file; the cap year is 2026-27, so he has no 2026-27 salary.
        roster = client.get(f"/api/v1/teams/{staged['team_a'].id}/roster").json()
        row = next(p for p in roster["roster"] if p["player_id"] == player.id)
        assert row["salary"] is None
        payroll = client.get(f"/api/v1/teams/{staged['team_a'].id}/payroll").json()
        entry = next(p for p in payroll["players"] if p["player_id"] == player.id)
        assert entry["salary"] is None
        assert player.full_name in payroll["players_missing_salary"]
        assert payroll["league_year"] == "2026-27" and payroll["roster_season"] == "2026-27"
        assert payroll["payroll_available"] is False and payroll["payroll"] is None
        assert payroll["payroll_known"] is not None and payroll["payroll_is_lower_bound"] is True

    def test_the_season_context_states_the_contract_state(self, db, staged, on):
        on(date(2026, 10, 18))
        db.query(ContractYear).delete()
        db.commit()
        context = tool_seasons(db)
        assert context["contract"] == {"season": "2026-27", "state": "missing", "rows": 0}
        assert any("never zero" in n for n in context["notes"])
        contract = db.scalars(select(Contract)).first()
        db.add(ContractYear(contract_id=contract.id, season="2026-27", salary=1_000_000))
        db.commit()
        assert tool_seasons(db)["contract"]["state"] == "available"


# ----------------------------------------------------------------- freshness and readiness


class TestFreshnessIsPerDataset:
    def test_rosters_are_judged_on_the_roster_season_and_statistics_on_the_performance_season(
        self, client, staged, on
    ):
        on(date(2026, 10, 18))
        body = client.get("/api/v1/data/freshness").json()
        assert body["season"]["roster_season"] == "2026-27"
        assert body["season"]["performance_season"] == "2025-26"
        datasets = body["datasets"]
        assert datasets["rosters"]["season"] == "2026-27"
        assert datasets["player_stats"]["season"] == "2025-26"
        assert datasets["contracts"]["season"] == "2026-27"

    def test_the_rollover_report_names_both_clocks(self, client, staged, on):
        on(date(2026, 10, 18))
        body = client.get("/api/v1/data/rollover").json()
        assert body["tools_read_season"] == "2025-26"
        assert body["tools_read_roster_season"] == "2026-27"

    def test_ingesting_the_roster_season_does_not_touch_the_previous_seasons_rows(self, db, staged):
        before = {
            (e.team_id, e.player_id, e.is_current)
            for e in db.scalars(select(RosterEntry).where(RosterEntry.season == "2025-26")).all()
        }
        assert before and all(current for _, _, current in before), "the history is intact"


class TestSeasonContextBlock:
    def test_the_block_separates_every_season_a_response_could_mean(self, db, staged, on):
        on(date(2026, 10, 18))
        context = tool_seasons(db)
        assert context["roster"]["season"] == "2026-27"
        assert context["roster"]["status"] == "provisional_preseason"
        assert context["roster"]["provisional"] is True
        assert context["roster"]["players"] == 31 and context["roster"]["teams"] == 2
        assert context["performance"]["season"] == "2025-26"
        assert context["performance"]["state"] == "completed_season"
        assert context["performance"]["is_baseline"] is True
        assert context["performance"]["behind_roster_season"] is True
        assert context["contract"]["season"] == "2026-27"
        assert context["projection"]["target_season"] == "2026-27"
        assert context["projection"]["input_season"] == "2025-26"
        assert context["projection"]["estimates_for_input_season"] is True

    def test_model_readiness_is_reported_not_assumed(self, db, staged, on):
        on(date(2026, 11, 4))  # the statistics have rolled over; no 2026-27 estimates exist
        context = tool_seasons(db, performance_season="2026-27")
        assert context["projection"]["estimates_for_input_season"] is False
        assert any("make train && make score" in n for n in context["notes"])

    def test_after_the_regular_season_starts_the_snapshot_stops_being_called_preseason(
        self, db, staged, on
    ):
        on(date(2026, 10, 25))
        assert tool_seasons(db)["roster"]["status"] == "regular_season_snapshot"

    def test_a_historical_roster_is_not_provisional(self, db, staged, on):
        on(date(2026, 10, 18))
        context = tool_seasons(db, roster_season="2025-26")
        assert context["roster"]["status"] == "historical_snapshot"
        assert context["roster"]["provisional"] is False


# -------------------------------------------------- after the statistics rollover (3 Nov 2026)


class TestAfterTheStatisticsRollover:
    """The roster did not move on 3 Nov (it moved on 1 July); the statistics did, to a season with none yet."""

    @pytest.fixture()
    def rolled(self, monkeypatch, on, staged):
        monkeypatch.delenv("CURRENT_SEASON", raising=False)
        on(date(2026, 11, 4))
        return staged

    def test_rosters_are_unchanged_by_the_rollover(self, client, rolled):
        for team in (rolled["team_a"], rolled["team_b"]):
            body = client.get(f"/api/v1/teams/{team.id}/roster").json()
            assert body["roster_season"] == "2026-27" and body["season"] == "2026-27"
        assert rolled["traded"].id in {
            p["player_id"] for p in client.get(f"/api/v1/teams/{rolled['team_b'].id}/roster").json()["roster"]
        }

    def test_no_2026_27_statistics_are_invented_or_carried_over(self, client, rolled):
        body = client.get(f"/api/v1/teams/{rolled['team_b'].id}/season-stats").json()
        assert body["season"] == "2026-27" and body["stats"] == {}, "last season's lines are not relabelled"
        roster = client.get(f"/api/v1/teams/{rolled['team_b'].id}/roster").json()["roster"]
        assert set(body["missing_player_ids"]) == {p["player_id"] for p in roster}

    def test_the_context_says_the_model_has_nothing_for_the_new_season(self, db, rolled):
        context = tool_seasons(db)
        assert context["performance"]["season"] == "2026-27"
        assert context["performance"]["is_baseline"] is False
        assert context["projection"]["estimates_for_input_season"] is False
        assert any("make train && make score" in n for n in context["notes"])

    def test_an_evaluation_names_the_estimates_it_is_still_reading(self, client, rolled):
        a, b = rolled["team_a"], rolled["team_b"]
        response = client.post(
            "/api/v1/trades/evaluate",
            json={
                "team_ids": [a.id, b.id],
                "player_moves": [
                    {"player_id": rolled["stayer"].id, "from_team_id": a.id, "to_team_id": b.id},
                    {"player_id": rolled["roster_b"][2].id, "from_team_id": b.id, "to_team_id": a.id},
                ],
                "pick_moves": [],
            },
        )
        assert response.status_code == 200, response.text
        basis = response.json()["season_basis"]
        assert basis["performance_season"] == "2026-27"
        assert basis["impact_estimate_seasons"] == ["2025-26"]
        # Roster and statistics seasons agree again, so no "needs describe the old team" caveat:
        # the needs for 2026-27 simply do not exist yet, and fit is withheld below.
        assert {limit["component"] for limit in basis["limitations"]} == {"performance"}
        for evaluation in response.json()["evaluations"].values():
            assert evaluation["confidence"] != "high"
            # No 2026-27 team needs exist, so fit is withheld, not scored from last season's.
            assert "fit" in evaluation["excluded_components"]

    def test_the_rollover_report_shows_what_is_missing(self, client, rolled):
        report = client.get("/api/v1/data/rollover").json()
        assert report["tools_read_season"] == "2026-27" and report["tools_read_roster_season"] == "2026-27"
        assert report["checks"]["impact_estimates"]["present"] is False
        assert report["checks"]["regular_season_player_stats"]["present"] is False
