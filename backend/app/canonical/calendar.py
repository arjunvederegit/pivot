"""Where a season is in its life: upcoming, in progress, or completed — as a function of a date.

Before R8.1 "the current season" was one environment string (``CURRENT_SEASON=2025-26``) edited by
hand. That cannot say the things a decision tool needs to say once the calendar moves on:

* On 18 October 2026 the *league* is in 2026-27 (preseason) but the last regular season anyone has
  statistics for is 2025-26. Neither "2026-27" nor "2025-26" is the whole truth; both are
  reported, and the prior season is labelled a **baseline**, never silently substituted.
* 2026-27 regular-season statistics do not exist until 20 October 2026. Their absence is
  *expected*, not an ingestion failure — and is never a zero.
* 2025-26 statistics, once the season is over, are *finalized* and do not go stale because a clock
  ran: they can still be corrected, so they are re-verified, not declared immutable.

**Dates.** A season's dates come, in order of authority, from (1) the league's published schedule
(``KNOWN_SCHEDULES`` — add one line per season when the NBA publishes it), (2) the ``season_calendar``
rows Pivot ingests from the game log, (3) an *estimate* from the league's habits (opening night is
the first Tuesday on or after 18 October; the regular season runs 173 days). An estimate says so
(``basis == "estimated"``) and is never presented as a published date.

**League year.** The league year, and therefore the season a roster belongs to, turns over on
1 July (free agency opens 30 June / 1 July). The season whose *statistics* tools should read turns
over only once its regular season has been underway ``grace_days`` (default 14): one night of games is
not a sample, and every tool keys its roster, statistics and model estimates on the same season.

This module is pure date arithmetic. It never reads the database or the clock on its own;
callers pass ``as_of``. ``today_in_league`` is the one place the clock is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from app.canonical.seasons import LEAGUE_TIMEZONE, Season, SeasonType, StatScope

#: Games before this share of the regular season has been played are not a sample for analysis.
DEFAULT_ROLLOVER_GRACE_DAYS = 14
#: Completed-season data retrieved this soon after the final game may still be missing the last
#: night's corrections; it counts as finalized only when retrieved after ``end + SETTLING``.
SETTLING_DAYS = 3


def today_in_league(now: datetime | None = None) -> date:
    """The current date on the league's (Eastern) calendar."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("today_in_league needs a timezone-aware datetime")
    return moment.astimezone(LEAGUE_TIMEZONE).date()


class SeasonPhase(StrEnum):
    #: Between the end of the playoffs and the next preseason (draft, free agency, summer league).
    OFFSEASON = "offseason"
    PRESEASON = "preseason"
    REGULAR_SEASON = "regular_season"
    PLAY_IN = "play_in"
    PLAYOFFS = "playoffs"


class ScopeState(StrEnum):
    """Whether the games a statistic aggregates have been played yet."""

    #: The first game of this season type is still in the future. No rows is the right answer.
    UPCOMING = "upcoming"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    #: The calendar does not say (a season type with no schedule, e.g. the NBA Cup folded into
    #: the regular season by NBA.com).
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SeasonSchedule:
    season: Season
    regular_start: date
    regular_end: date
    preseason_start: date
    play_in_start: date
    playoffs_start: date
    playoffs_end: date
    #: ``"league_published"`` | ``"ingested_games"`` | ``"estimated"``.
    basis: str

    @property
    def is_estimated(self) -> bool:
        return self.basis == "estimated"

    def window(self, season_type: SeasonType) -> tuple[date, date] | None:
        """First and last day (inclusive) of a season type, or ``None`` if it has no window."""
        if season_type is SeasonType.PRESEASON:
            return self.preseason_start, self.regular_start - timedelta(days=1)
        if season_type is SeasonType.REGULAR:
            return self.regular_start, self.regular_end
        if season_type is SeasonType.PLAY_IN:
            return self.play_in_start, self.playoffs_start - timedelta(days=1)
        if season_type is SeasonType.PLAYOFFS:
            return self.playoffs_start, self.playoffs_end
        return None


#: The league's published regular-season windows. One line per season, added when the NBA
#: announces it; anything absent falls back to ingested games, then to an estimate.
#: 2025-26: ingested game log (21 Oct 2025 – 12 Apr 2026). 2026-27: pr.nba.com schedule release.
KNOWN_SCHEDULES: dict[int, tuple[date, date]] = {
    2025: (date(2025, 10, 21), date(2026, 4, 12)),
    2026: (date(2026, 10, 20), date(2027, 4, 11)),
}


def _first_tuesday_on_or_after(day: date) -> date:
    return day + timedelta(days=(1 - day.weekday()) % 7)


def _build(season: Season, start: date, end: date, basis: str) -> SeasonSchedule:
    return SeasonSchedule(
        season=season,
        regular_start=start,
        regular_end=end,
        preseason_start=start - timedelta(days=18),
        play_in_start=end + timedelta(days=3),
        playoffs_start=end + timedelta(days=7),
        playoffs_end=date(end.year, 6, 20),
        basis=basis,
    )


def schedule_for(
    season: Season,
    *,
    ingested: tuple[date, date] | None = None,
) -> SeasonSchedule:
    """The best available schedule for ``season``.

    ``ingested`` is ``(first_game_date, last_game_date)`` of the regular season from the
    ``season_calendar`` table. It is trusted for the *start*; for the end it is trusted only when it
    is plausibly the whole season (a season still in progress would report "today" as its last game).
    """
    known = KNOWN_SCHEDULES.get(season.start_year)
    if known is not None:
        return _build(season, known[0], known[1], "league_published")
    estimated_start = _first_tuesday_on_or_after(date(season.start_year, 10, 18))
    estimated_end = estimated_start + timedelta(days=173)
    if ingested is not None:
        start, end = ingested
        if (end - start).days >= 150:
            return _build(season, start, end, "ingested_games")
        return _build(season, start, estimated_start + timedelta(days=173), "estimated")
    return _build(season, estimated_start, estimated_end, "estimated")


def league_season_for(day: date) -> Season:
    """The season the *league year* is in on ``day`` (turns over 1 July)."""
    return Season(day.year if day.month >= 7 else day.year - 1)


def scope_state(
    schedule: SeasonSchedule, season_type: SeasonType, as_of: date
) -> ScopeState:
    window = schedule.window(season_type)
    if window is None:
        return ScopeState.UNKNOWN
    first, last = window
    if as_of < first:
        return ScopeState.UPCOMING
    if as_of > last:
        return ScopeState.COMPLETED
    return ScopeState.IN_PROGRESS


def phase_on(schedule: SeasonSchedule, as_of: date) -> SeasonPhase:
    """The phase of ``schedule``'s own season on ``as_of`` (OFFSEASON outside its windows)."""
    if schedule.preseason_start <= as_of < schedule.regular_start:
        return SeasonPhase.PRESEASON
    if schedule.regular_start <= as_of <= schedule.regular_end:
        return SeasonPhase.REGULAR_SEASON
    if schedule.play_in_start <= as_of < schedule.playoffs_start:
        return SeasonPhase.PLAY_IN
    if schedule.playoffs_start <= as_of <= schedule.playoffs_end:
        return SeasonPhase.PLAYOFFS
    # The few days between the last regular-season game and the play-in are still the playoffs'
    # run-up, not an offseason.
    if schedule.regular_end < as_of < schedule.play_in_start:
        return SeasonPhase.PLAY_IN
    return SeasonPhase.OFFSEASON


@dataclass(frozen=True)
class SeasonContext:
    """Answers "which season, what phase, and is there data yet?" for one date."""

    as_of: date
    #: The season the league is in (rosters, transactions, the cap year).
    league_season: Season
    #: The season's phase *as of* ``as_of`` — for ``league_season``.
    phase: SeasonPhase
    #: The season the analytical tools read (rosters, statistics, model estimates).
    analysis_season: Season
    #: The most recent season whose regular season is over (always <= analysis season).
    last_completed_season: Season
    #: True when ``analysis_season`` is behind ``league_season`` — prior-season information is
    #: being used as context and every payload built on it must say so.
    analysis_is_baseline: bool
    regular_stats_state: ScopeState
    preseason_stats_state: ScopeState
    schedule: SeasonSchedule
    grace_days: int

    @property
    def regular_season_stats_expected(self) -> bool:
        """Whether 2026-27-style regular-season rows *should* exist for the league season."""
        return self.regular_stats_state in (ScopeState.IN_PROGRESS, ScopeState.COMPLETED)

    @property
    def rollover_date(self) -> date:
        """The first day the tools read the league season's statistics."""
        return self.schedule.regular_start + timedelta(days=self.grace_days)

    def describe(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "league_season": self.league_season.label,
            "phase": self.phase.value,
            "analysis_season": self.analysis_season.label,
            "analysis_is_baseline": self.analysis_is_baseline,
            "last_completed_season": self.last_completed_season.label,
            "regular_season_stats": self.regular_stats_state.value,
            "preseason_stats": self.preseason_stats_state.value,
            "regular_season_starts": self.schedule.regular_start.isoformat(),
            "regular_season_ends": self.schedule.regular_end.isoformat(),
            "rollover_date": self.rollover_date.isoformat(),
            "schedule_basis": self.schedule.basis,
        }


def season_context(
    as_of: date,
    *,
    grace_days: int = DEFAULT_ROLLOVER_GRACE_DAYS,
    ingested: dict[int, tuple[date, date]] | None = None,
) -> SeasonContext:
    league = league_season_for(as_of)
    ingested = ingested or {}
    schedule = schedule_for(league, ingested=ingested.get(league.start_year))
    analysis = (
        league
        if as_of >= schedule.regular_start + timedelta(days=grace_days)
        else league.previous()
    )
    regular_state = scope_state(schedule, SeasonType.REGULAR, as_of)
    last_completed = (
        league if regular_state is ScopeState.COMPLETED else league.previous()
    )
    return SeasonContext(
        as_of=as_of,
        league_season=league,
        phase=phase_on(schedule, as_of),
        analysis_season=analysis,
        last_completed_season=last_completed,
        analysis_is_baseline=analysis != league,
        regular_stats_state=regular_state,
        preseason_stats_state=scope_state(schedule, SeasonType.PRESEASON, as_of),
        schedule=schedule,
        grace_days=grace_days,
    )


def state_of_scope(
    scope: StatScope,
    as_of: date,
    *,
    ingested: dict[int, tuple[date, date]] | None = None,
) -> ScopeState:
    """Has the season type in ``scope`` begun / finished on ``as_of``?"""
    schedule = schedule_for(scope.season, ingested=(ingested or {}).get(scope.season.start_year))
    return scope_state(schedule, scope.season_type, as_of)


def settled(schedule: SeasonSchedule, season_type: SeasonType) -> date | None:
    """The date after which data about a completed season type counts as final."""
    window = schedule.window(season_type)
    return None if window is None else window[1] + timedelta(days=SETTLING_DAYS)
