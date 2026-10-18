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

The window per dataset is configuration (``Settings.*_stale_after_seconds``), not a constant
buried in a query.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.canonical.stats import Basis
from app.config import get_settings
from app.core.provenance import describe_providers


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    UNKNOWN = "unknown"


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

    @property
    def is_usable(self) -> bool:
        return self.freshness in (Freshness.FRESH, Freshness.STALE)


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
) -> SourceMeta:
    """Build a ``SourceMeta`` from aggregates already computed (min/max/count in SQL)."""
    providers = sorted({p for p in providers if p})
    window = stale_after if stale_after is not None else stale_after_seconds(dataset)
    oldest, newest = as_utc(oldest), as_utc(newest)
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
        notes=list(notes),
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
