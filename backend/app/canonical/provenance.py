"""Source transparency: where a payload's data came from, how old it is, and how complete.

``Provenance`` (``app.api.schemas``) says *which provider* a row came from and *when it was
retrieved*. That is necessary and not enough: a reader also needs to know whether the data is
**fresh enough to act on**, whether the payload **covers everything it should**, and whether
the figures were **observed or estimated**. ``SourceMeta`` carries those, as one object that
every data-bearing endpoint can attach under the same key (``data_status``).

Freshness is a *state*, not a timestamp the client is trusted to interpret:

* ``fresh``    — every contributing row was retrieved inside the dataset's window.
* ``stale``    — at least one contributing row is older than the window. The *oldest* row
  decides, so a table that is 29 teams current and one team three months old is stale; the
  newest-row maximum that ``/data-health`` reports would call it fresh.
* ``missing``  — the payload has no rows at all. Not an empty list presented as an answer.
* ``unknown``  — rows exist but none carries a retrieval time.
* ``finalized`` — (R8.1) the season is over and every contributing row was retrieved *after* it
  settled. Completed-season figures do not go stale because a clock ran; they are re-verified, not
  declared immutable (upstream corrections happen), so a very old retrieval earns an advisory note,
  never a warning.
* ``not_yet_available`` — (R8.1) the games this dataset aggregates have not been played yet (2026-27
  regular-season statistics before opening night). No rows is the *right* answer, not a failure.

A completed season whose rows were retrieved **before** it ended is ``stale``, however recent the
retrieval: it is a partial season and has not been re-fetched since the last game.

The window per dataset is configuration (``Settings.*_stale_after_seconds``), not a constant
buried in a query. What a dataset's *lifecycle* means is ``DATASET_POLICIES``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.canonical.calendar import (
    ScopeState,
    SeasonContext,
    schedule_for,
    scope_state,
    season_context,
    settled,
    today_in_league,
)
from app.canonical.seasons import InvalidSeasonError, Season, SeasonType
from app.canonical.stats import Basis
from app.config import get_settings
from app.core.provenance import describe_providers


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    UNKNOWN = "unknown"
    FINALIZED = "finalized"
    NOT_YET_AVAILABLE = "not_yet_available"


#: States in which the figures can be shown to a user. ``not_yet_available`` and ``missing``
#: have nothing to show; ``unknown`` has rows of undetermined age.
USABLE = frozenset({Freshness.FRESH, Freshness.STALE, Freshness.FINALIZED})
#: States that need no one's attention: the data is as good as it can be for the date.
HEALTHY = frozenset({Freshness.FRESH, Freshness.FINALIZED, Freshness.NOT_YET_AVAILABLE})


@dataclass(frozen=True)
class DatasetPolicy:
    """What "up to date" means for one kind of dataset.

    ``finalizes`` — a *completed* season's figures stop ageing once retrieved after the season
    settled (statistics, standings, games). Rosters, injuries and contracts describe *now*, so a
    completed season's snapshot is history but the live one never finalizes.
    ``cadence`` — how often a healthy deployment refreshes it (worker schedule), in words.
    """

    name: str
    finalizes: bool
    season_scoped: bool
    cadence: str


DATASET_POLICIES: dict[str, DatasetPolicy] = {
    p.name: p
    for p in (
        DatasetPolicy("rosters", finalizes=False, season_scoped=True, cadence="every 6 hours"),
        DatasetPolicy("standings", finalizes=True, season_scoped=True, cadence="every 6 hours in season"),
        DatasetPolicy("player_stats", finalizes=True, season_scoped=True, cadence="daily in season"),
        DatasetPolicy("team_stats", finalizes=True, season_scoped=True, cadence="daily in season"),
        DatasetPolicy("games", finalizes=True, season_scoped=True, cadence="daily in season"),
        DatasetPolicy("injuries", finalizes=False, season_scoped=False, cadence="every few hours"),
        DatasetPolicy("contracts", finalizes=False, season_scoped=False, cadence="monthly snapshot"),
        DatasetPolicy("historical_stats", finalizes=True, season_scoped=False, cadence="once, then re-verified"),
    )
}

def as_utc(value: datetime | None) -> datetime | None:
    """Timezone-aware UTC. SQLite hands timestamps back naive; they were stored as UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def stale_after_seconds(dataset: str) -> int:
    """The freshness window for a named dataset."""
    settings = get_settings()
    windows = {
        "rosters": settings.nba_api_stale_after_seconds,
        "standings": settings.nba_api_stale_after_seconds,
        "player_stats": settings.stats_stale_after_seconds,
        "team_stats": settings.stats_stale_after_seconds,
        "games": settings.stats_stale_after_seconds,
        "contracts": settings.contracts_stale_after_seconds,
        "injuries": settings.injuries_stale_after_seconds,
    }
    return windows.get(dataset, settings.nba_api_stale_after_seconds)


class SourceMeta(BaseModel):
    dataset: str
    providers: list[str] = Field(default_factory=list)
    upstream: str
    season: str | None = None
    season_type: str | None = None
    #: Newest and oldest retrieval among the contributing rows.
    retrieved_at: datetime | None = None
    oldest_retrieved_at: datetime | None = None
    #: When the membership/fact being described took effect, where the dataset has one
    #: (a roster snapshot's earliest ``valid_from``). Observation time, not a league record.
    effective_from: datetime | None = None
    freshness: Freshness
    age_seconds: int | None = None
    stale_after_seconds: int | None = None
    present_count: int = 0
    #: What the payload should contain, when that is knowable (30 teams for a league roster).
    #: ``None`` means "no expectation exists", not "complete".
    expected_count: int | None = None
    completeness: float | None = None
    basis: Basis = Basis.OBSERVED
    notes: list[str] = Field(default_factory=list)
    #: R8.1. Whether the games behind the figures have been played: upcoming | in_progress |
    #: completed | unknown. ``None`` for datasets with no season scope (contracts, injuries).
    scope_state: str | None = None
    #: One sentence saying *why* ``freshness`` has the value it has.
    status_reason: str | None = None
    #: How often a healthy deployment refreshes this dataset.
    cadence: str | None = None
    #: Date of the newest game the data covers, where the dataset knows it (games). This is
    #: when basketball happened, not when Pivot looked (``retrieved_at``).
    observed_through: date | None = None
    #: True when these figures are a prior season used as context because the league's own
    #: season has no statistics yet. They are never to be read as current-season results.
    is_baseline: bool = False
    #: The most recent refresh attempt for this dataset (job, status, finished_at, error). A
    #: failing refresh is visible here even while the stored rows still read ``fresh``.
    last_refresh: dict[str, Any] | None = None
    refresh_failing: bool = False

    @property
    def is_usable(self) -> bool:
        return self.freshness in USABLE

    @property
    def needs_attention(self) -> bool:
        return self.freshness not in HEALTHY or self.refresh_failing


def assess_freshness(
    oldest: datetime | None, present: int, stale_after: int, now: datetime | None = None
) -> tuple[Freshness, int | None]:
    """The freshness state and the age (seconds) of the *oldest* contributing row."""
    if present == 0:
        return Freshness.MISSING, None
    if oldest is None:
        return Freshness.UNKNOWN, None
    age = int(((now or datetime.now(UTC)) - oldest).total_seconds())
    return (Freshness.STALE if age > stale_after else Freshness.FRESH), max(age, 0)


def _scope_for(dataset: str, season: str | None, season_type: str | None) -> tuple[Season, SeasonType] | None:
    policy = DATASET_POLICIES.get(dataset)
    if policy is None or not policy.season_scoped or not season:
        return None
    try:
        return Season.parse(season), SeasonType(season_type or SeasonType.REGULAR.value)
    except (InvalidSeasonError, ValueError):
        return None


def assess_lifecycle(
    dataset: str,
    *,
    season: str | None,
    season_type: str | None,
    count: int,
    oldest: datetime | None,
    newest: datetime | None,
    stale_after: int,
    now: datetime,
    context: SeasonContext | None = None,
) -> tuple[Freshness, int | None, ScopeState | None, str, bool, list[str]]:
    """Freshness *as the calendar sees it*: ``(freshness, age, scope_state, reason, baseline, notes)``.

    The decision, in order:

    1. No rows. If the games have not been played (``upcoming``) that is ``not_yet_available``;
       otherwise ``missing``.
    2. Rows, no retrieval times: ``unknown``.
    3. The scope is **completed** and the dataset finalizes: ``finalized`` if the *oldest* row was
       retrieved after the season settled (end + 3 days), else ``stale`` — a partial season that
       has not been re-fetched. Age alone never makes a completed season stale.
    4. Everything else (in progress, a live roster, contracts, injuries, a season with no calendar):
       ``stale`` past the dataset's window, otherwise ``fresh``.
    """
    policy = DATASET_POLICIES.get(dataset)
    ctx = context or season_context(today_in_league(now))
    scope = _scope_for(dataset, season, season_type)
    state: ScopeState | None = None
    baseline = False
    notes: list[str] = []
    if scope is not None:
        season_obj, type_obj = scope
        state = scope_state(schedule_for(season_obj), type_obj, ctx.as_of)
        # Rosters describe the present: the live roster season is "in progress" however old the
        # calendar says the *regular season* is, and only strictly earlier seasons are history.
        if dataset == "rosters":
            state = (
                ScopeState.COMPLETED
                if season_obj < ctx.analysis_season
                else ScopeState.IN_PROGRESS
            )
        baseline = season_obj == ctx.analysis_season and ctx.analysis_is_baseline
        if baseline:
            notes.append(
                f"{season_obj} is the most recent completed season, shown as context while the "
                f"league is in {ctx.league_season} ({ctx.phase.value}); it is not "
                f"{ctx.league_season} performance"
            )
    if count == 0:
        if state is ScopeState.UPCOMING:
            when = schedule_for(Season.parse(season)).window(SeasonType(season_type or "regular"))  # type: ignore[arg-type]
            return (
                Freshness.NOT_YET_AVAILABLE,
                None,
                state,
                f"{season} {season_type or 'regular'}-season games begin "
                f"{when[0].isoformat() if when else 'later'}; no figures exist yet, and none are "
                "substituted",
                baseline,
                notes,
            )
        return Freshness.MISSING, None, state, "no rows are stored for this dataset", baseline, notes
    if oldest is None:
        return Freshness.UNKNOWN, None, state, "rows exist but none records when it was retrieved", baseline, notes
    age = max(int((now - oldest).total_seconds()), 0)
    finalizes = policy is not None and (
        policy.finalizes or (dataset == "rosters" and state is ScopeState.COMPLETED)
    )
    if state is ScopeState.COMPLETED and finalizes and scope:
        season_obj, type_obj = scope
        cutoff = settled(schedule_for(season_obj), type_obj)
        cutoff_at = (
            datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=UTC) if cutoff else None
        )
        if cutoff_at is not None and oldest < cutoff_at:
            return (
                Freshness.STALE,
                age,
                state,
                f"the oldest row was retrieved {oldest.date().isoformat()}, before {season} "
                "settled; re-fetch once to finalize it",
                baseline,
                notes,
            )
        reverify_days = _reverify_days()
        if newest is not None and now - newest > timedelta(days=reverify_days):
            notes.append(
                f"last re-verified against the source {newest.date().isoformat()}; completed "
                "seasons can still be corrected upstream"
            )
        return (
            Freshness.FINALIZED,
            age,
            state,
            f"{season} is complete and every row was retrieved after it settled",
            baseline,
            notes,
        )
    window_label = _window_label(stale_after)
    if age > stale_after:
        return (
            Freshness.STALE,
            age,
            state,
            f"the oldest row is {_window_label(age)} old; this dataset is considered stale after "
            f"{window_label}",
            baseline,
            notes,
        )
    return Freshness.FRESH, age, state, f"every row was retrieved within {window_label}", baseline, notes


def _reverify_days() -> int:
    return get_settings().finalized_reverify_after_days


def _window_label(seconds: int) -> str:
    if seconds >= 172_800:
        return f"{seconds // 86_400} days"
    if seconds >= 7_200:
        return f"{seconds // 3_600} hours"
    return f"{max(seconds // 60, 1)} minutes"


def source_meta_from_aggregate(
    dataset: str,
    *,
    count: int,
    oldest: datetime | None,
    newest: datetime | None,
    providers: Iterable[str] = (),
    effective_from: datetime | None = None,
    season: str | None = None,
    season_type: str | None = None,
    expected_count: int | None = None,
    basis: Basis = Basis.OBSERVED,
    notes: Iterable[str] = (),
    stale_after: int | None = None,
    now: datetime | None = None,
    observed_through: date | None = None,
    context: SeasonContext | None = None,
) -> SourceMeta:
    """Build a ``SourceMeta`` from aggregates already computed (min/max/count in SQL).

    Season-scoped datasets (``DATASET_POLICIES``) are judged against the calendar — see
    ``assess_lifecycle``. Everything else keeps the original age-against-window rule.
    """
    providers = sorted({p for p in providers if p})
    window = stale_after if stale_after is not None else stale_after_seconds(dataset)
    oldest, newest = as_utc(oldest), as_utc(newest)
    moment = now or datetime.now(UTC)
    policy = DATASET_POLICIES.get(dataset)
    scope_state_value: ScopeState | None = None
    reason: str | None = None
    baseline = False
    lifecycle_notes: list[str] = []
    if policy is not None:
        freshness, age, scope_state_value, reason, baseline, lifecycle_notes = assess_lifecycle(
            dataset,
            season=season,
            season_type=season_type,
            count=count,
            oldest=oldest,
            newest=newest,
            stale_after=window,
            now=moment,
            context=context,
        )
    else:
        freshness, age = assess_freshness(oldest, count, window, now)
    completeness = None
    if expected_count:
        completeness = round(min(count / expected_count, 1.0), 4)
    return SourceMeta(
        dataset=dataset,
        providers=providers,
        upstream=describe_providers(providers),
        season=season,
        season_type=season_type,
        retrieved_at=newest,
        oldest_retrieved_at=oldest,
        effective_from=as_utc(effective_from),
        freshness=freshness,
        age_seconds=age,
        stale_after_seconds=window,
        present_count=count,
        expected_count=expected_count,
        completeness=completeness,
        basis=basis,
        notes=[*notes, *lifecycle_notes],
        scope_state=scope_state_value.value if scope_state_value else None,
        status_reason=reason,
        cadence=policy.cadence if policy else None,
        observed_through=observed_through,
        is_baseline=baseline,
    )


def build_source_meta(
    dataset: str,
    rows: Iterable[Any],
    *,
    season: str | None = None,
    season_type: str | None = None,
    expected_count: int | None = None,
    basis: Basis = Basis.OBSERVED,
    notes: Iterable[str] = (),
    stale_after: int | None = None,
    now: datetime | None = None,
) -> SourceMeta:
    """Summarise ``ProvenanceMixin`` rows into one ``SourceMeta``.

    ``present_count`` counts the rows given; callers that group rows (for instance a team's
    roster) pass the rows they actually served.
    """
    rows = list(rows)
    retrievals = [
        t for t in (as_utc(getattr(r, "source_retrieved_at", None)) for r in rows) if t is not None
    ]
    effective = [
        t for t in (as_utc(getattr(r, "valid_from", None)) for r in rows) if t is not None
    ]
    all_notes = list(notes)
    if rows and len(retrievals) < len(rows):
        all_notes.append(
            f"{len(rows) - len(retrievals)} of {len(rows)} rows carry no retrieval time"
        )
    return source_meta_from_aggregate(
        dataset,
        count=len(rows),
        oldest=min(retrievals) if retrievals else None,
        newest=max(retrievals) if retrievals else None,
        providers=[getattr(r, "source_provider", None) or "" for r in rows],
        effective_from=min(effective) if effective else None,
        season=season,
        season_type=season_type,
        expected_count=expected_count,
        basis=basis,
        notes=all_notes,
        stale_after=stale_after,
        now=now,
    )
