"""R8.1 — nba_api spoken in the canonical contract.

``fetch_result`` must say *what happened* (answered / answered-nothing / did not answer) and the
record converters must carry nba_api rows into the provider-independent types without changing a
value the database stores.
"""

from datetime import date

import pytest

from app.canonical.records import ProviderStatus
from app.canonical.seasons import Season, SeasonType
from app.integrations.nba_api import canonical as nba
from app.integrations.nba_api.exceptions import (
    ProviderBlockedError,
    ProviderEmptyError,
    ProviderSchemaError,
    ProviderThrottledError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _call(value=None, exc=None):
    async def run():
        if exc:
            raise exc
        return value

    return run


class TestFetchResult:
    def test_rows_are_ok(self):
        result, error = nba.fetch_result("x", _call([{"a": 1}]), season="2025-26")
        assert result.status is ProviderStatus.OK and error is None
        assert result.records == ({"a": 1},)
        assert result.meta.params == {"season": "2025-26"}

    def test_an_empty_list_is_an_answer_of_nothing(self):
        result, error = nba.fetch_result("x", _call([]))
        assert result.status is ProviderStatus.EMPTY and result.has_answer and error is None
        assert result.require_data() == ()

    def test_a_well_formed_empty_response_is_empty_not_a_failure(self):
        result, error = nba.fetch_result("x", _call(exc=ProviderEmptyError("x", "zero rows")))
        assert result.status is ProviderStatus.EMPTY and result.has_answer and error is None

    @pytest.mark.parametrize(
        ("exc", "status"),
        [
            (ProviderBlockedError("x", "403"), ProviderStatus.UNAVAILABLE),
            (ProviderTimeoutError("x", "timed out"), ProviderStatus.UNAVAILABLE),
            (ProviderUnavailableError("x", "circuit open"), ProviderStatus.UNAVAILABLE),
            (ProviderThrottledError("x", "429"), ProviderStatus.RATE_LIMITED),
            (ProviderSchemaError("x", "missing columns"), ProviderStatus.SCHEMA_ERROR),
            (RuntimeError("boom"), ProviderStatus.UNAVAILABLE),
        ],
    )
    def test_failures_are_classified_and_hand_back_the_original_exception(self, exc, status):
        result, error = nba.fetch_result("x", _call(exc=exc))
        assert result.status is status and not result.has_answer
        assert error is exc, "callers re-raise the real error, not a wrapper"
        assert result.records == ()

    def test_an_empty_error_is_still_a_schema_error_for_older_handlers(self):
        assert issubclass(ProviderEmptyError, ProviderSchemaError)


class TestRecords:
    def test_standing_and_game_carry_scope_and_provider_ids(self):
        standing = nba.standing_record(
            {"nba_team_id": 1610612738, "season": "2025-26", "wins": 60, "losses": 22,
             "conference_rank": 2, "details": {"HOME": "34-7", "ROAD": "26-15"}}
        )
        assert (standing.team_provider_id, standing.scope.season, standing.scope.season_type) == (
            "1610612738", Season(2025), SeasonType.REGULAR)
        assert (standing.home_record, standing.road_record) == ("34-7", "26-15")
        game = nba.game_record(
            {"nba_game_id": "0022500001", "season": "2025-26", "game_date": date(2025, 10, 21),
             "home_nba_team_id": 1, "away_nba_team_id": 2, "home_score": 110, "away_score": 108})
        assert game and game.home_team_provider_id == "1" and game.season == Season(2025)

    def test_a_game_with_a_missing_side_is_not_invented(self):
        assert nba.game_record({"nba_game_id": "x", "season": "2025-26", "game_date": date(2025, 10, 21),
                                "home_nba_team_id": 1, "away_nba_team_id": None}) is None

    def test_stat_values_keep_numbers_drop_labels_and_never_turn_missing_into_zero(self):
        record = nba.player_stat_record(
            {"nba_player_id": 7, "nba_team_id": 3, "season": "2025-26", "stat_type": "base",
             "games_played": 70, "stats": {"PTS": 21.5, "PLAYER_NAME": "X", "FG3_PCT": None,
                                           "AST": float("nan"), "ACTIVE": True}})
        assert record.values == {"PTS": 21.5, "FG3_PCT": None, "AST": None}
        assert record.aggregation == "per_game" and record.basis == "observed"
        assert record.team_provider_id == "3"

    def test_estimated_rows_are_marked_as_modelled(self):
        assert nba.player_stat_record(
            {"nba_player_id": 7, "season": "2025-26", "stat_type": "estimated", "stats": {}}
        ).basis == "estimated"

    def test_season_type_is_carried_not_defaulted_silently(self):
        record = nba.team_stat_record({"nba_team_id": 1, "season": "2026-27", "stat_type": "base"},
                                      SeasonType.PRESEASON)
        assert record.scope.season_type is SeasonType.PRESEASON

    def test_roster_rows_become_players_with_a_team_hint(self):
        player = nba.roster_player_record(
            {"nba_player_id": 9, "nba_team_id": 4, "player_name": "A B", "position": "G"})
        assert (player.provider, player.provider_id, player.team_provider_id) == ("nba_api", "9", "4")

    def test_team_record(self):
        team = nba.team_record({"nba_team_id": 1610612738, "full_name": "Boston Celtics",
                                "abbreviation": "BOS", "nickname": "Celtics", "city": "Boston"})
        assert team.provider_id == "1610612738" and team.abbreviation == "BOS"


class TestNeutralSiteGames:
    """Found by the R8.1 live run: both rows of a neutral-site game read "X @ Y"."""

    def _log(self, rows):
        import pandas as pd

        return pd.DataFrame(rows)

    def test_both_teams_and_their_own_scores_survive_in_either_row_order(self):
        from app.integrations.nba_api.normalizers import normalize_game_log

        det = {"GAME_ID": "0022500147", "TEAM_ID": 10, "GAME_DATE": "2025-11-01",
               "MATCHUP": "DET @ DAL", "WL": "W", "PTS": 122}
        dal = {"GAME_ID": "0022500147", "TEAM_ID": 20, "GAME_DATE": "2025-11-01",
               "MATCHUP": "DAL @ DET", "WL": "L", "PTS": 110}
        a = normalize_game_log(self._log([det, dal]), "2025-26")
        b = normalize_game_log(self._log([dal, det]), "2025-26")
        assert a == b, "row order must not change the answer"
        (game,) = a
        assert {game["home_nba_team_id"], game["away_nba_team_id"]} == {10, 20}
        scores = {game["home_nba_team_id"]: game["home_score"], game["away_nba_team_id"]: game["away_score"]}
        assert scores == {10: 122, 20: 110}, "each team keeps its own score"

    def test_an_ordinary_game_is_unchanged(self):
        from app.integrations.nba_api.normalizers import normalize_game_log

        rows = [{"GAME_ID": "1", "TEAM_ID": 1, "GAME_DATE": "2025-10-21", "MATCHUP": "AAA vs. BBB", "PTS": 100},
                {"GAME_ID": "1", "TEAM_ID": 2, "GAME_DATE": "2025-10-21", "MATCHUP": "BBB @ AAA", "PTS": 90}]
        (game,) = normalize_game_log(self._log(rows), "2025-26")
        assert (game["home_nba_team_id"], game["home_score"], game["away_nba_team_id"], game["away_score"]) == (1, 100, 2, 90)
