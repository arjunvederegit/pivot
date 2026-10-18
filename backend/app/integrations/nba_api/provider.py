"""NBADataProvider protocol and its nba_api implementation.

Domain code depends on the protocol, never on nba_api directly; the concrete provider
returns normalized records plus provenance meta. nba_api is synchronous, so the async
protocol methods delegate to worker threads."""

from typing import Protocol

import anyio

from app.canonical.seasons import SeasonType
from app.config import get_settings

from . import endpoints as ep
from . import normalizers as nz


class NBADataProvider(Protocol):
    async def fetch_teams(self) -> list[dict]: ...

    async def fetch_players(self) -> list[dict]: ...

    async def fetch_rosters(self, season: str) -> list[dict]: ...

    async def fetch_standings(self, season: str) -> list[dict]: ...

    async def fetch_player_stats(
        self, season: str, season_type: SeasonType = SeasonType.REGULAR
    ) -> list[dict]: ...

    async def fetch_team_stats(
        self, season: str, season_type: SeasonType = SeasonType.REGULAR
    ) -> list[dict]: ...

    async def fetch_games(self, season: str) -> list[dict]: ...

    async def fetch_live_scoreboard(self) -> list[dict]: ...


class SwarNBAApiProvider:
    """Primary provider: NBA.com via the swar/nba_api package."""

    name = "nba_api"

    async def fetch_teams(self) -> list[dict]:
        records = await anyio.to_thread.run_sync(ep.fetch_static_teams)
        return [nz.normalize_static_team(r) for r in records]

    async def fetch_players(self) -> list[dict]:
        records = await anyio.to_thread.run_sync(ep.fetch_static_players)
        return [nz.normalize_static_player(r) for r in records]

    async def fetch_rosters(self, season: str) -> list[dict]:
        teams = await anyio.to_thread.run_sync(ep.fetch_static_teams)
        rows: list[dict] = []
        for team in teams:
            df, meta = await anyio.to_thread.run_sync(ep.fetch_team_roster, team["id"], season)
            provenance = nz.provenance_from_meta(meta)
            for _, row in df.iterrows():
                rows.append({**nz.normalize_roster_row(row, season), **provenance})
        return rows

    async def fetch_standings(self, season: str) -> list[dict]:
        df, meta = await anyio.to_thread.run_sync(ep.fetch_standings, season)
        provenance = nz.provenance_from_meta(meta)
        return [
            {**nz.normalize_standing_row(row, season), **provenance} for _, row in df.iterrows()
        ]

    async def fetch_player_stats(
        self, season: str, season_type: SeasonType = SeasonType.REGULAR
    ) -> list[dict]:
        rows: list[dict] = []
        for measure, stat_type in (("Base", "base"), ("Advanced", "advanced")):
            df, meta = await anyio.to_thread.run_sync(
                ep.fetch_player_stats, season, measure, "PerGame", season_type
            )
            provenance = nz.provenance_from_meta(meta)
            rows.extend(
                {**nz.normalize_player_stat_row(row, season, stat_type), **provenance}
                for _, row in df.iterrows()
            )
        return rows

    async def fetch_team_stats(
        self, season: str, season_type: SeasonType = SeasonType.REGULAR
    ) -> list[dict]:
        rows: list[dict] = []
        for measure, stat_type in (("Base", "base"), ("Advanced", "advanced")):
            df, meta = await anyio.to_thread.run_sync(
                ep.fetch_team_stats, season, measure, "PerGame", season_type
            )
            provenance = nz.provenance_from_meta(meta)
            rows.extend(
                {**nz.normalize_team_stat_row(row, season, stat_type), **provenance}
                for _, row in df.iterrows()
            )
        return rows

    async def fetch_games(self, season: str) -> list[dict]:
        df, meta = await anyio.to_thread.run_sync(ep.fetch_league_game_log, season)
        provenance = nz.provenance_from_meta(meta)
        return [{**g, **provenance} for g in nz.normalize_game_log(df, season)]

    async def fetch_player_estimated_metrics(self, season: str) -> list[dict]:
        df, meta = await anyio.to_thread.run_sync(ep.fetch_player_estimated_metrics, season)
        provenance = nz.provenance_from_meta(meta)
        return [
            {**nz.normalize_player_stat_row(row, season, "estimated"), **provenance}
            for _, row in df.iterrows()
        ]

    async def fetch_live_scoreboard(self) -> list[dict]:
        if not get_settings().live_data_enabled:
            return []
        data = await anyio.to_thread.run_sync(ep.fetch_live_scoreboard)
        return data.get("scoreboard", {}).get("games", [])


def get_provider() -> SwarNBAApiProvider:
    return SwarNBAApiProvider()
