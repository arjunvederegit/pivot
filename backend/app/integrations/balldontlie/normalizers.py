"""BALLDONTLIE payloads -> Pivot canonical records.

The row models below are written from the provider's OpenAPI document
(``https://www.balldontlie.io/openapi/nba.yml``, retrieved 2026-10-18) and deliberately
tolerant: unknown fields are ignored and nearly every field is optional, because the
document says the data is best-effort and may be incomplete. What is *required* is exactly
what a record cannot exist without (the provider id). A row that fails that is rejected with
a reason; it is never repaired.

These functions are pure — no network, no database — so they are tested against fixtures.

Two rules carried from the canonical layer:

* **The request's scope is authoritative.** A game row says only ``postseason: true|false``;
  it cannot say play-in vs playoffs vs NBA Cup. The caller asked for a specific
  ``season_type`` and the row is filed under *that* — and a row that contradicts the request
  (``postseason`` disagrees) is rejected rather than quietly relabelled.
* **Missing is ``None``.** Empty strings, NaN and non-numerics become ``None``, never 0.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.canonical.records import (
    ContractAggregateRecord,
    ContractYearRecord,
    GameRecord,
    InjuryRecord,
    PlayerRecord,
    PlayerStatRecord,
    StandingRecord,
    TeamRecord,
    TeamStatRecord,
)
from app.canonical.seasons import Season, SeasonType, StatScope
from app.canonical.stats import numeric

PROVIDER = "balldontlie"


class RowRejectedError(ValueError):
    """A provider row that cannot become a canonical record."""


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class BdlTeam(_Row):
    id: int
    abbreviation: str
    full_name: str
    city: str | None = None
    name: str | None = None
    conference: str | None = None
    division: str | None = None


class BdlPlayer(_Row):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    position: str | None = None
    height: str | None = None
    weight: str | None = None
    jersey_number: str | None = None
    college: str | None = None
    country: str | None = None
    draft_year: int | None = None
    draft_round: int | None = None
    draft_number: int | None = None
    #: ``/players`` and ``/players/active`` nest the team object; ``/player_injuries`` and
    #: ``/lineups`` carry only ``team_id`` (observed live, 2026-10-18). Both are accepted.
    team: BdlTeam | None = None
    team_id: int | None = None

    @field_validator("draft_year", "draft_round", "draft_number", mode="before")
    @classmethod
    def _blank_int(cls, value: Any) -> Any:
        return None if value in ("", None) else value


class BdlGame(_Row):
    id: int
    date: str
    season: int
    status: str | None = None
    postseason: bool | None = None
    home_team_score: int | None = None
    visitor_team_score: int | None = None
    home_team: BdlTeam
    visitor_team: BdlTeam


class BdlStanding(_Row):
    team: BdlTeam
    wins: int
    losses: int
    season: int | None = None
    conference_rank: int | None = None
    division_rank: int | None = None
    home_record: str | None = None
    road_record: str | None = None


class BdlSeasonAverage(_Row):
    """``/season_averages/{category}``: observed live, the player is a nested object.

    The OpenAPI document's flat ``player_id`` describes a different (legacy) shape; the
    live endpoint returns ``{"player": {...}, "season", "season_type", "stats": {...}}``.
    Both are accepted so a flat row from the legacy route still parses.
    """

    player_id: int | None = None
    player: BdlPlayer | None = None
    season: int
    season_type: str | None = None
    stats: dict[str, Any]

    @model_validator(mode="after")
    def _identify(self) -> BdlSeasonAverage:
        if self.player_id is None and self.player is not None:
            self.player_id = self.player.id
        if self.player_id is None:
            raise ValueError("player_id: neither player_id nor player.id is present")
        return self


class BdlTeamSeasonStat(_Row):
    team: BdlTeam
    season: int
    season_type: str | None = None
    stats: dict[str, Any]


class BdlContract(_Row):
    id: int | None = None
    player: BdlPlayer | None = None
    player_id: int
    season: int
    team_id: int | None = None
    cap_hit: int | None = None
    base_salary: int | None = None
    total_cash: int | None = None


class BdlContractAggregate(_Row):
    player_id: int
    team_id: int | None = None
    start_year: int | None = None
    end_year: int | None = None
    contract_type: str | None = None
    contract_status: str | None = None
    contract_years: int | None = None
    total_value: int | None = None
    average_salary: int | None = None
    guaranteed_at_signing: int | None = None
    total_guaranteed: int | None = None
    signed_using: str | None = None
    free_agent_year: int | None = None
    free_agent_status: str | None = None
    #: Observed live as ``null`` or a list of strings (``"2022-23: Club Option (deadline ...)"``);
    #: the OpenAPI document says object. A dict is still accepted.
    contract_notes: list[str] | dict[str, Any] | None = None


class BdlInjury(_Row):
    player: BdlPlayer
    status: str
    description: str | None = None
    return_date: str | None = None


def _validate(model: type[_Row], raw: Any, what: str) -> Any:
    if not isinstance(raw, dict):
        raise RowRejectedError(f"{what}: row is not an object")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise RowRejectedError(f"{what}: {loc}: {first['msg']}") from exc


_HEIGHT = re.compile(r"^\s*(\d)\s*-\s*(\d{1,2})\s*$")


def parse_height_inches(value: str | None) -> int | None:
    """``"6-8"`` -> 80. Anything else (blank, ``"6'8"``, ``"203cm"``) is ``None``, not a guess."""
    match = _HEIGHT.match(value or "")
    return int(match.group(1)) * 12 + int(match.group(2)) if match else None


def parse_weight_lbs(value: str | None) -> int | None:
    text = (value or "").strip()
    return int(text) if text.isdigit() and 0 < int(text) < 700 else None


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def team_record(raw: Any) -> TeamRecord:
    t: BdlTeam = _validate(BdlTeam, raw, "team")
    return TeamRecord(
        provider=PROVIDER,
        provider_id=str(t.id),
        abbreviation=t.abbreviation,
        full_name=t.full_name,
        city=_clean(t.city),
        nickname=_clean(t.name),
        conference=_clean(t.conference),
        division=_clean(t.division),
    )


def player_record(raw: Any, *, is_active: bool | None = None) -> PlayerRecord:
    p: BdlPlayer = _validate(BdlPlayer, raw, "player")
    first, last = _clean(p.first_name), _clean(p.last_name)
    full = " ".join(x for x in (first, last) if x)
    if not full:
        raise RowRejectedError(f"player {p.id}: no name")
    return PlayerRecord(
        provider=PROVIDER,
        provider_id=str(p.id),
        full_name=full,
        first_name=first,
        last_name=last,
        position=_clean(p.position),
        height_inches=parse_height_inches(p.height),
        weight_lbs=parse_weight_lbs(p.weight),
        jersey_number=_clean(p.jersey_number),
        college=_clean(p.college),
        country=_clean(p.country),
        draft_year=p.draft_year,
        draft_round=p.draft_round,
        draft_number=p.draft_number,
        team_provider_id=(
            str(p.team.id) if p.team else (str(p.team_id) if p.team_id is not None else None)
        ),
        is_active=is_active,
    )


def game_record(raw: Any, requested: StatScope) -> GameRecord:
    g: BdlGame = _validate(BdlGame, raw, "game")
    if g.season != requested.season.provider_year:
        raise RowRejectedError(
            f"game {g.id}: season {g.season} does not match requested {requested.season.label}"
        )
    if g.postseason is not None:
        is_post = requested.season_type in (SeasonType.PLAYOFFS, SeasonType.PLAY_IN)
        if g.postseason != is_post and requested.season_type != SeasonType.IN_SEASON_TOURNAMENT:
            raise RowRejectedError(
                f"game {g.id}: postseason={g.postseason} contradicts requested "
                f"season_type={requested.season_type.value}"
            )
    try:
        game_date = date.fromisoformat(g.date[:10])
    except ValueError as exc:
        raise RowRejectedError(f"game {g.id}: unparseable date {g.date!r}") from exc
    return GameRecord(
        provider=PROVIDER,
        provider_id=str(g.id),
        season=requested.season,
        season_type=requested.season_type,
        game_date=game_date,
        home_team_provider_id=str(g.home_team.id),
        away_team_provider_id=str(g.visitor_team.id),
        home_score=g.home_team_score,
        away_score=g.visitor_team_score,
        status=g.status,
    )


def standing_record(raw: Any, requested: StatScope) -> StandingRecord:
    s: BdlStanding = _validate(BdlStanding, raw, "standing")
    if s.season is not None and s.season != requested.season.provider_year:
        raise RowRejectedError(
            f"standing for team {s.team.id}: season {s.season} != requested {requested.season.label}"
        )
    return StandingRecord(
        provider=PROVIDER,
        team_provider_id=str(s.team.id),
        scope=requested,
        wins=s.wins,
        losses=s.losses,
        conference_rank=s.conference_rank,
        division_rank=s.division_rank,
        home_record=_clean(s.home_record),
        road_record=_clean(s.road_record),
    )


def _values(stats: dict[str, Any]) -> dict[str, float | None]:
    """Numeric figures only, absent -> ``None``. Non-numeric entries (labels) are dropped."""
    out: dict[str, float | None] = {}
    for key, value in stats.items():
        if isinstance(value, (dict, list)):
            continue
        number = numeric(value)
        if number is not None or value is None:
            out[key] = number
    return out


def _embedded_player(raw: Any) -> PlayerRecord | None:
    """The provider embeds a full player object in season-average and contract rows. It is used to
    link identities that ``/players/active`` does not list; a malformed one is simply absent."""
    try:
        return player_record(raw.get("player")) if isinstance(raw, dict) and raw.get("player") else None
    except RowRejectedError:
        return None


def player_stat_record(raw: Any, requested: StatScope, stat_group: str) -> PlayerStatRecord:
    row: BdlSeasonAverage = _validate(BdlSeasonAverage, raw, "season average")
    if row.season != requested.season.provider_year:
        raise RowRejectedError(
            f"player {row.player_id}: season {row.season} != requested {requested.season.label}"
        )
    if row.season_type and row.season_type != requested.season_type.balldontlie_label:
        raise RowRejectedError(
            f"player {row.player_id}: season_type {row.season_type!r} contradicts requested "
            f"{requested.season_type.balldontlie_label!r}"
        )
    values = _values(row.stats)
    gp = numeric(row.stats.get("gp", row.stats.get("games_played")))
    return PlayerStatRecord(
        provider=PROVIDER,
        player_provider_id=str(row.player_id),
        scope=requested,
        stat_group=stat_group,
        # The documentation does not state the aggregation of /season_averages/{type}.
        # "unspecified" is the honest value until the trial's reconciliation against NBA.com
        # per-game rows (see docs/r8/balldontlie-goat-evaluation.md, test S2) settles it.
        aggregation="unspecified",
        values=values,
        games_played=int(gp) if gp is not None else None,
        player=_embedded_player(raw),
    )


def team_stat_record(raw: Any, requested: StatScope, stat_group: str) -> TeamStatRecord:
    row: BdlTeamSeasonStat = _validate(BdlTeamSeasonStat, raw, "team season stat")
    if row.season != requested.season.provider_year:
        raise RowRejectedError(
            f"team {row.team.id}: season {row.season} != requested {requested.season.label}"
        )
    return TeamStatRecord(
        provider=PROVIDER,
        team_provider_id=str(row.team.id),
        scope=requested,
        stat_group=stat_group,
        aggregation="unspecified",
        values=_values(row.stats),
    )


def contract_year_record(raw: Any) -> ContractYearRecord:
    c: BdlContract = _validate(BdlContract, raw, "contract")
    return ContractYearRecord(
        provider=PROVIDER,
        player_provider_id=str(c.player_id),
        team_provider_id=str(c.team_id) if c.team_id is not None else None,
        season=Season.from_provider_year(c.season),
        cap_hit=c.cap_hit,
        base_salary=c.base_salary,
        total_cash=c.total_cash,
        provider_contract_id=str(c.id) if c.id is not None else None,
        player=_embedded_player(raw),
        player_name=(
            " ".join(x for x in (_clean(c.player.first_name), _clean(c.player.last_name)) if x)
            or None
            if c.player
            else None
        ),
    )


def _notes(raw: list[str] | dict[str, Any] | None) -> dict[str, Any]:
    """Verbatim: a list becomes ``{"lines": [...]}``; nothing is parsed into option flags."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {"lines": [str(line) for line in raw]}


def contract_aggregate_record(raw: Any) -> ContractAggregateRecord:
    c: BdlContractAggregate = _validate(BdlContractAggregate, raw, "contract aggregate")
    return ContractAggregateRecord(
        provider=PROVIDER,
        player_provider_id=str(c.player_id),
        team_provider_id=str(c.team_id) if c.team_id is not None else None,
        start_season=Season.from_provider_year(c.start_year) if c.start_year else None,
        end_season=Season.from_provider_year(c.end_year) if c.end_year else None,
        contract_type=_clean(c.contract_type),
        contract_status=_clean(c.contract_status),
        contract_years=c.contract_years,
        total_value=c.total_value,
        average_salary=c.average_salary,
        guaranteed_at_signing=c.guaranteed_at_signing,
        total_guaranteed=c.total_guaranteed,
        signed_using=_clean(c.signed_using),
        free_agent_year=c.free_agent_year,
        free_agent_status=_clean(c.free_agent_status),
        notes=_notes(c.contract_notes),
    )


def injury_record(raw: Any) -> InjuryRecord:
    i: BdlInjury = _validate(BdlInjury, raw, "injury")
    return InjuryRecord(
        provider=PROVIDER,
        player_provider_id=str(i.player.id),
        status=i.status,
        description=_clean(i.description),
        return_date=_clean(i.return_date),
        team_provider_id=(
            str(i.player.team.id)
            if i.player.team
            else (str(i.player.team_id) if i.player.team_id is not None else None)
        ),
    )
