"""The BALLDONTLIE provider: canonical records out, an honest ``ProviderResult`` for every call.

Nothing here is required for Pivot to run. With no ``BALLDONTLIE_API_KEY`` every method
returns ``NOT_CONFIGURED`` without making a request. No Pivot tool imports this module
directly; consumers depend on ``app.canonical.records`` and receive records.

**Live behaviour is unverified.** This adapter is written against the provider's published
OpenAPI document and tested against *synthetic* fixtures shaped like it
(``tests/fixtures/balldontlie/``). No live response has been observed by this code. What the
live API actually returns — coverage, accuracy, history, whether ``/season_averages`` are
per-game — is what the trial evaluation (``docs/r8/balldontlie-goat-evaluation.md``) exists to
find out.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from app.canonical.records import (
    CallMeta,
    ContractAggregateRecord,
    ContractYearRecord,
    GameRecord,
    InjuryRecord,
    PlayerRecord,
    PlayerStatRecord,
    ProviderResult,
    ProviderStatus,
    StandingRecord,
    TeamRecord,
    TeamStatRecord,
)
from app.canonical.seasons import Season, SeasonType, StatScope
from app.config import get_settings
from app.integrations.balldontlie import normalizers as nz
from app.integrations.balldontlie.client import (
    PROVIDER,
    BalldontlieClient,
    BalldontlieError,
    PageResult,
)

T = TypeVar("T")

#: The plan an endpoint needs, from the provider's "Account Tiers" table (docs retrieved
#: 2026-10-18). `trial` carries GOAT's endpoints at 5 requests/minute.
ENDPOINT_MIN_TIER: dict[str, str] = {
    "teams": "free",
    "players": "free",
    "games": "free",
    "players/active": "all_star",
    "stats": "all_star",
    "player_injuries": "all_star",
    "standings": "goat",
    "season_averages": "goat",
    "team_season_averages": "goat",
    "stats/advanced": "goat",
    "box_scores": "goat",
    "lineups": "goat",
    "contracts": "goat",
    "contracts/aggregate": "goat",
    "plays": "goat",
}
_TIER_RANK = {"free": 0, "all_star": 1, "trial": 2, "goat": 2, "all_access": 3}


#: Category -> type pairings for ``/season_averages/{category}`` as published in the API
#: reference ("Season Averages", retrieved 2026-10-18). The OpenAPI document leaves ``type`` a free
#: string, and a wrong pairing costs a request to learn it, so the pairing is checked locally.
PLAYER_STAT_TYPES: dict[str, tuple[str, ...]] = {
    "general": ("base", "advanced", "usage", "scoring", "defense", "misc"),
    "clutch": ("base", "advanced", "misc", "scoring", "usage"),
    "defense": (
        "overall", "2_pointers", "3_pointers", "greater_than_15ft", "less_than_10ft", "less_than_6ft",
    ),
    "shooting": ("5ft_range", "by_zone"),
    "playtype": (
        "cut", "handoff", "isolation", "offrebound", "offscreen", "postup", "prballhandler",
        "prrollman", "spotup", "transition", "misc",
    ),
    "tracking": (
        "painttouch", "efficiency", "speeddistance", "defense", "elbowtouch", "posttouch",
        "passing", "drives", "rebounding", "catchshoot", "pullupshot", "possessions",
    ),
    "hustle": (),  # no type parameter
    "shotdashboard": ("overall", "pullups", "catch_and_shoot", "less_than_10_ft"),
}
#: Team categories (OpenAPI description of ``/team_season_averages/{category}``).
TEAM_STAT_TYPES: dict[str, tuple[str, ...]] = {
    "general": ("base", "advanced", "scoring", "misc", "opponent", "defense", "violations"),
    "clutch": ("base", "advanced", "misc", "scoring"),
    "shooting": ("by_zone_base", "by_zone_opponent", "5ft_range_base", "5ft_range_opponent"),
    "playtype": PLAYER_STAT_TYPES["playtype"],
    "tracking": PLAYER_STAT_TYPES["tracking"],
    "hustle": (),
    "shotdashboard": PLAYER_STAT_TYPES["shotdashboard"],
}


class InvalidStatPairingError(ValueError):
    """A (category, type) pair the provider does not publish; raised before any request."""


def check_stat_pairing(table: dict[str, tuple[str, ...]], category: str, stat_type: str | None) -> None:
    if category not in table:
        raise InvalidStatPairingError(f"unknown category {category!r}; known: {sorted(table)}")
    allowed = table[category]
    if not allowed:
        if stat_type:
            raise InvalidStatPairingError(f"category {category!r} takes no type (got {stat_type!r})")
    elif stat_type not in allowed:
        raise InvalidStatPairingError(
            f"type {stat_type!r} is not published for category {category!r}; known: {list(allowed)}"
        )


def tier_allows(tier: str, endpoint: str) -> bool:
    """Whether a plan includes an endpoint, per the published table (advisory only)."""
    return _TIER_RANK[tier] >= _TIER_RANK[ENDPOINT_MIN_TIER[endpoint]]


class BalldontlieProvider:
    name = PROVIDER

    def __init__(self, client: BalldontlieClient):
        self.client = client

    @classmethod
    def from_settings(cls) -> BalldontlieProvider:
        settings = get_settings()
        return cls(
            BalldontlieClient(
                settings.balldontlie_api_key.get_secret_value(),
                tier=settings.balldontlie_tier,
                base_url=settings.balldontlie_base_url,
                prefix=settings.balldontlie_api_prefix,
                timeout=settings.balldontlie_timeout_seconds,
                max_retries=settings.balldontlie_max_retries,
                ledger_path=settings.balldontlie_request_log or None,
            )
        )

    @property
    def configured(self) -> bool:
        return self.client.configured

    # ----------------------------------------------------------------- core runner

    def _run(
        self,
        endpoint: str,
        params: dict[str, Any],
        fetch: Callable[[], PageResult],
        convert: Callable[[Any], T],
    ) -> ProviderResult[T]:
        started = datetime.now(UTC)
        before = self.client.request_count
        try:
            page = fetch()
        except BalldontlieError as exc:
            meta = CallMeta(
                PROVIDER,
                endpoint,
                params,
                started,
                requests=self.client.request_count - before,
            )
            if exc.status == ProviderStatus.EMPTY:
                return ProviderResult.ok([], meta)
            return ProviderResult.failed(exc.status, meta, str(exc))
        records: list[T] = []
        rejected: list[str] = []
        for raw in page.records:
            try:
                records.append(convert(raw))
            except nz.RowRejectedError as exc:
                rejected.append(str(exc))
        meta = CallMeta(
            PROVIDER, endpoint, params, started, pages=page.pages, requests=page.requests
        )
        warnings = list(page.warnings)
        if rejected:
            warnings.append(f"{len(rejected)} of {len(page.records)} rows rejected: {rejected[0]}")
        if page.records and not records:
            return ProviderResult.failed(
                ProviderStatus.SCHEMA_ERROR,
                meta,
                f"every one of {len(page.records)} rows was rejected: {rejected[0]}",
            )
        return ProviderResult.ok(records, meta, tuple(warnings))

    def _list(
        self,
        endpoint: str,
        path: str,
        params: dict[str, Any],
        convert: Callable[[Any], T],
        *,
        max_pages: int = 100,
        sample: bool = False,
    ) -> ProviderResult[T]:
        return self._run(
            endpoint,
            params,
            lambda: self.client.paginate(path, params, max_pages=max_pages, sample=sample),
            convert,
        )

    def _single_page(
        self, endpoint: str, path: str, params: dict[str, Any], convert: Callable[[Any], T]
    ) -> ProviderResult[T]:
        def fetch() -> PageResult:
            payload = self.client.get(path, params)
            data = payload.get("data")
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise BalldontlieError(ProviderStatus.SCHEMA_ERROR, path, "response has no 'data'")
            return PageResult([r for r in data if isinstance(r, dict)], 1, 1)

        return self._run(endpoint, params, fetch, convert)

    # ----------------------------------------------------------------- identity data

    def teams(self) -> ProviderResult[TeamRecord]:
        return self._single_page("teams", "/teams", {}, nz.team_record)

    def players(
        self, *, search: str | None = None, team_ids: list[int] | None = None, max_pages: int = 100
    ) -> ProviderResult[PlayerRecord]:
        params = {"search": search, "team_ids[]": team_ids}
        return self._list("players", "/players", params, nz.player_record, max_pages=max_pages)

    def active_players(self, *, max_pages: int = 20) -> ProviderResult[PlayerRecord]:
        return self._list(
            "players/active",
            "/players/active",
            {},
            lambda raw: nz.player_record(raw, is_active=True),
            max_pages=max_pages,
        )

    # --------------------------------------------------------------------- games

    def games(
        self,
        season: Season | str | int,
        season_type: SeasonType = SeasonType.REGULAR,
        *,
        max_pages: int = 100,
        sample: bool = False,
    ) -> ProviderResult[GameRecord]:
        """Games for one season of ONE explicit type.

        ``season_type`` is always sent. The provider's own default, when it is omitted, is
        *every non-preseason type*, which would silently fold playoff games into a
        regular-season pull.
        """
        scope = StatScope(Season.coerce(season), season_type)
        params = {
            "seasons[]": [scope.season.provider_year],
            "season_type": season_type.balldontlie_label,
        }
        return self._list(
            "games", "/games", params, lambda raw: nz.game_record(raw, scope), max_pages=max_pages,
            sample=sample,
        )

    def standings(self, season: Season | str | int) -> ProviderResult[StandingRecord]:
        scope = StatScope.regular(season)
        return self._single_page(
            "standings",
            "/standings",
            {"season": scope.season.provider_year},
            lambda raw: nz.standing_record(raw, scope),
        )

    # ---------------------------------------------------------------- statistics

    def player_season_stats(
        self,
        season: Season | str | int,
        season_type: SeasonType = SeasonType.REGULAR,
        *,
        category: str = "general",
        stat_type: str | None = "base",
        player_ids: list[int] | None = None,
        max_pages: int = 50,
        sample: bool = False,
    ) -> ProviderResult[PlayerStatRecord]:
        check_stat_pairing(PLAYER_STAT_TYPES, category, stat_type)
        scope = StatScope(Season.coerce(season), season_type)
        params = {
            "season": scope.season.provider_year,
            "season_type": season_type.balldontlie_label,
            "type": stat_type or None,
            # The API reference spells the array ``player_ids[]`` (the OpenAPI document omits the
            # brackets); verified live 2026-10-18 with a single-player request.
            "player_ids[]": player_ids,
        }
        group = f"{category}/{stat_type}" if stat_type else category
        return self._list(
            "season_averages",
            f"/season_averages/{category}",
            params,
            lambda raw: nz.player_stat_record(raw, scope, group),
            max_pages=max_pages,
            sample=sample,
        )

    def team_season_stats(
        self,
        season: Season | str | int,
        season_type: SeasonType = SeasonType.REGULAR,
        *,
        category: str = "general",
        stat_type: str | None = "base",
        team_ids: list[int] | None = None,
        max_pages: int = 5,
        sample: bool = False,
    ) -> ProviderResult[TeamStatRecord]:
        check_stat_pairing(TEAM_STAT_TYPES, category, stat_type)
        scope = StatScope(Season.coerce(season), season_type)
        params = {
            "season": scope.season.provider_year,
            "season_type": season_type.balldontlie_label,
            "type": stat_type or None,
            "team_ids[]": team_ids,
        }
        group = f"{category}/{stat_type}" if stat_type else category
        return self._list(
            "team_season_averages",
            f"/team_season_averages/{category}",
            params,
            lambda raw: nz.team_stat_record(raw, scope, group),
            max_pages=max_pages,
            sample=sample,
        )

    # ----------------------------------------------------------------- availability

    def injuries(self, *, max_pages: int = 10) -> ProviderResult[InjuryRecord]:
        return self._list("player_injuries", "/player_injuries", {}, nz.injury_record, max_pages=max_pages)

    # ----------------------------------------------------------------- contracts

    def team_contracts(
        self, team_provider_id: int, season: Season | str | int | None = None
    ) -> ProviderResult[ContractYearRecord]:
        params = {
            "team_id": team_provider_id,
            "season": Season.coerce(season).provider_year if season is not None else None,
        }
        return self._single_page("contracts", "/contracts/teams", params, nz.contract_year_record)

    def player_contracts(
        self, player_provider_id: int, seasons: list[Season | str | int] | None = None
    ) -> ProviderResult[ContractYearRecord]:
        params = {
            "player_id": player_provider_id,
            "seasons[]": [Season.coerce(s).provider_year for s in seasons] if seasons else None,
        }
        return self._list("contracts", "/contracts/players", params, nz.contract_year_record, max_pages=5)

    def contract_aggregate(
        self, player_provider_id: int
    ) -> ProviderResult[ContractAggregateRecord]:
        return self._single_page(
            "contracts/aggregate",
            "/contracts/players/aggregate",
            {"player_id": player_provider_id},
            nz.contract_aggregate_record,
        )
