"""Typed wrappers around the nba_api endpoint classes used in production.

Each wrapper returns (DataFrame, provenance_meta). Import of nba_api endpoint modules
happens inside functions so unit tests can run without the package's network layer."""

from typing import Any

import pandas as pd

from app.canonical.seasons import SeasonType

from ..client import get_client


def fetch_static_teams() -> list[dict[str, Any]]:
    """Package-bundled static team identity records (no network)."""
    from nba_api.stats.static import teams

    return teams.get_teams()


def fetch_static_players() -> list[dict[str, Any]]:
    """Package-bundled static player identity records (no network)."""
    from nba_api.stats.static import players

    return players.get_players()


def fetch_team_roster(nba_team_id: int, season: str) -> tuple[pd.DataFrame, dict]:
    def build(common: dict[str, Any]):
        from nba_api.stats.endpoints import commonteamroster

        return commonteamroster.CommonTeamRoster(team_id=nba_team_id, season=season, **common)

    return get_client().fetch_dataframe(
        "CommonTeamRoster", build, cache_key=f"roster:{nba_team_id}:{season}"
    )


def fetch_standings(season: str) -> tuple[pd.DataFrame, dict]:
    def build(common: dict[str, Any]):
        from nba_api.stats.endpoints import leaguestandingsv3

        return leaguestandingsv3.LeagueStandingsV3(season=season, **common)

    return get_client().fetch_dataframe("LeagueStandingsV3", build, cache_key=f"standings:{season}")


def _type_kwargs(season_type: SeasonType) -> dict[str, str]:
    """``season_type_all_star`` for a non-regular scope. The regular season sends nothing, so its
    requests (and cache keys) are exactly what they were before R8.1."""
    return {} if season_type is SeasonType.REGULAR else {"season_type_all_star": season_type.nba_com_label}


def _type_key(season_type: SeasonType) -> str:
    return "" if season_type is SeasonType.REGULAR else f":{season_type.value}"


def fetch_player_stats(
    season: str,
    measure_type: str = "Base",
    per_mode: str = "PerGame",
    season_type: SeasonType = SeasonType.REGULAR,
) -> tuple[pd.DataFrame, dict]:
    def build(common: dict[str, Any]):
        from nba_api.stats.endpoints import leaguedashplayerstats

        return leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            measure_type_detailed_defense=measure_type,
            per_mode_detailed=per_mode,
            **_type_kwargs(season_type),
            **common,
        )

    return get_client().fetch_dataframe(
        "LeagueDashPlayerStats",
        build,
        cache_key=f"playerstats:{season}:{measure_type}:{per_mode}{_type_key(season_type)}",
    )


def fetch_team_stats(
    season: str,
    measure_type: str = "Base",
    per_mode: str = "PerGame",
    season_type: SeasonType = SeasonType.REGULAR,
) -> tuple[pd.DataFrame, dict]:
    def build(common: dict[str, Any]):
        from nba_api.stats.endpoints import leaguedashteamstats

        return leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            measure_type_detailed_defense=measure_type,
            per_mode_detailed=per_mode,
            **_type_kwargs(season_type),
            **common,
        )

    return get_client().fetch_dataframe(
        "LeagueDashTeamStats",
        build,
        cache_key=f"teamstats:{season}:{measure_type}:{per_mode}{_type_key(season_type)}",
    )


def fetch_league_game_log(season: str) -> tuple[pd.DataFrame, dict]:
    def build(common: dict[str, Any]):
        from nba_api.stats.endpoints import leaguegamelog

        return leaguegamelog.LeagueGameLog(season=season, player_or_team_abbreviation="T", **common)

    return get_client().fetch_dataframe("LeagueGameLog", build, cache_key=f"gamelog:{season}")


def fetch_player_estimated_metrics(season: str) -> tuple[pd.DataFrame, dict]:
    def build(common: dict[str, Any]):
        from nba_api.stats.endpoints import playerestimatedmetrics

        return playerestimatedmetrics.PlayerEstimatedMetrics(season=season, **common)

    return get_client().fetch_dataframe(
        "PlayerEstimatedMetrics", build, cache_key=f"estmetrics:{season}"
    )


def fetch_live_scoreboard() -> dict[str, Any]:
    """Live scoreboard from cdn.nba.com. Only called when LIVE_DATA_ENABLED=true."""
    from nba_api.live.nba.endpoints import scoreboard

    return scoreboard.ScoreBoard().get_dict()
