"""Pivot's one definition of an NBA season, a season type and a statistical scope.

Before R8 a season was a bare ``String(10)`` repeated across a dozen tables, compared by
equality, with the only format check a SQL ``LIKE '____-__'`` in the data-quality job. Every
provider spells it differently — NBA.com ``"2025-26"``, BALLDONTLIE the integer ``2025`` —
and nothing said which spelling Pivot meant or what happens at the boundary.

**Convention.** A season is identified by the calendar year it *starts* in and labelled
``"YYYY-YY"``: the 2025-26 season is ``Season(2025)``, label ``"2025-26"``. That is NBA.com's
spelling and what every existing row stores. A provider integer is always the start year,
so ``2025`` and ``"2025-26"`` name the same season; BALLDONTLIE's documentation examples
and the NBA's own ``SeasonYear`` agree.

**Season type is part of a statistic's identity.** Regular-season and playoff production
are different populations — different opponents, minutes and usage — and a figure that does
not say which it is cannot be compared with one that does. ``SeasonType`` is therefore a
first-class value, and nothing in this module defaults it silently: ``StatScope`` requires it.

**Dates.** ``Game.game_date`` is the date on the *Eastern* calendar the NBA publishes
(``GAME_DATE`` in NBA.com's game log). A tip-off at 22:00 Pacific is 05:00 UTC the next day;
``game_date_from_utc`` converts to the league's date so a late game is not filed under the
wrong day.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

#: The calendar the league publishes game dates on.
LEAGUE_TIMEZONE = ZoneInfo("America/New_York")

_LABEL = re.compile(r"^(\d{4})-(\d{2})$")


class InvalidSeasonError(ValueError):
    """A season label or year that is not a real NBA season spelling."""


@dataclass(frozen=True, order=True)
class Season:
    """A season, identified by the year it starts in."""

    start_year: int

    def __post_init__(self) -> None:
        # The league's first season began in 1946; the upper bound is a typo guard, not a
        # claim about when the league ends.
        if not isinstance(self.start_year, int) or not 1946 <= self.start_year <= 2100:
            raise InvalidSeasonError(f"not an NBA season start year: {self.start_year!r}")

    @property
    def label(self) -> str:
        return f"{self.start_year}-{(self.start_year + 1) % 100:02d}"

    def __str__(self) -> str:
        return self.label

    @classmethod
    def parse(cls, label: str) -> Season:
        """Strict parse of ``"YYYY-YY"``. ``"2025-27"``, ``"2025"`` and ``"25-26"`` are errors."""
        match = _LABEL.match(label.strip()) if isinstance(label, str) else None
        if match is None:
            raise InvalidSeasonError(f"not a 'YYYY-YY' season label: {label!r}")
        start, end = int(match.group(1)), int(match.group(2))
        if end != (start + 1) % 100:
            raise InvalidSeasonError(
                f"season label {label!r} does not span consecutive years "
                f"(expected {start}-{(start + 1) % 100:02d})"
            )
        return cls(start)

    @classmethod
    def from_provider_year(cls, year: int) -> Season:
        """A provider's integer season, which is the *start* year (``2025`` = 2025-26)."""
        if isinstance(year, bool) or not isinstance(year, int):
            raise InvalidSeasonError(f"a provider season must be an integer year, got {year!r}")
        return cls(year)

    @classmethod
    def coerce(cls, value: Season | str | int) -> Season:
        if isinstance(value, Season):
            return value
        if isinstance(value, int):
            return cls.from_provider_year(value)
        return cls.parse(value)

    @property
    def provider_year(self) -> int:
        return self.start_year

    def next(self) -> Season:
        return Season(self.start_year + 1)

    def previous(self) -> Season:
        return Season(self.start_year - 1)


def is_valid_season_label(label: object) -> bool:
    try:
        Season.parse(label)  # type: ignore[arg-type]
    except InvalidSeasonError:
        return False
    return True


class SeasonType(StrEnum):
    """What kind of games a statistic aggregates. Never defaulted."""

    PRESEASON = "preseason"
    REGULAR = "regular"
    PLAY_IN = "play_in"
    PLAYOFFS = "playoffs"
    #: The NBA Cup / in-season tournament. BALLDONTLIE exposes it as its own type ("ist");
    #: NBA.com folds the group and knockout games into the regular season.
    IN_SEASON_TOURNAMENT = "ist"

    @classmethod
    def from_nba_com(cls, label: str) -> SeasonType:
        try:
            return _NBA_COM_TO_TYPE[label]
        except KeyError:
            raise InvalidSeasonError(f"unknown NBA.com season type: {label!r}") from None

    @property
    def nba_com_label(self) -> str:
        return _TYPE_TO_NBA_COM[self]

    @classmethod
    def from_balldontlie(cls, label: str) -> SeasonType:
        try:
            return _BDL_TO_TYPE[label]
        except KeyError:
            raise InvalidSeasonError(f"unknown BALLDONTLIE season type: {label!r}") from None

    @property
    def balldontlie_label(self) -> str:
        return _TYPE_TO_BDL[self]


_NBA_COM_TO_TYPE = {
    "Pre Season": SeasonType.PRESEASON,
    "Regular Season": SeasonType.REGULAR,
    "PlayIn": SeasonType.PLAY_IN,
    "Playoffs": SeasonType.PLAYOFFS,
    "IST": SeasonType.IN_SEASON_TOURNAMENT,
}
_TYPE_TO_NBA_COM = {v: k for k, v in _NBA_COM_TO_TYPE.items()}

_BDL_TO_TYPE = {
    "preseason": SeasonType.PRESEASON,
    "regular": SeasonType.REGULAR,
    "playin": SeasonType.PLAY_IN,
    "playoffs": SeasonType.PLAYOFFS,
    "ist": SeasonType.IN_SEASON_TOURNAMENT,
}
_TYPE_TO_BDL = {v: k for k, v in _BDL_TO_TYPE.items()}


@dataclass(frozen=True)
class StatScope:
    """The population a statistic is computed over: one season, one season type."""

    season: Season
    season_type: SeasonType

    @classmethod
    def regular(cls, season: Season | str | int) -> StatScope:
        return cls(Season.coerce(season), SeasonType.REGULAR)

    def __str__(self) -> str:
        return f"{self.season.label}/{self.season_type.value}"


def game_date_from_utc(moment: datetime) -> date:
    """The league-calendar date of a UTC instant.

    ``moment`` must be timezone-aware; a naive datetime is ambiguous and is refused rather
    than assumed to be UTC.
    """
    if moment.tzinfo is None:
        raise ValueError("game_date_from_utc needs a timezone-aware datetime")
    return moment.astimezone(LEAGUE_TIMEZONE).date()
