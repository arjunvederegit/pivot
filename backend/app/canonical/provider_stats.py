"""Reading a provider's statistics back out (R8.3): provider-explicit, scope-explicit, missing-explicit.

The counterpart of ``stat_access.player_stat_rows`` for figures that live in
``provider_stat_snapshots``. It exists so no tool queries that table (or the provider) on its own:

* the **provider, scope and statistic group** are required arguments, so a regular-season request
  can never return a playoff row and a BALLDONTLIE figure can never be mistaken for NBA.com's;
* what the figures are **per** (``aggregation``) travels with the result;
* players with **no row** come back as ``missing_player_ids`` — absent, not zero;
* a ``SourceMeta`` reports how fresh and how complete the set is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.provenance import SourceMeta, build_source_meta
from app.canonical.rosters import resolve_season
from app.canonical.seasons import Season, SeasonType, StatScope
from app.canonical.stats import numeric
from app.db.models import ProviderStatSnapshot


@dataclass
class ProviderStatRows:
    provider: str
    scope: StatScope
    stat_group: str
    entity_type: str
    rows: dict[str, ProviderStatSnapshot]
    requested_ids: list[str]
    meta: SourceMeta
    missing_ids: list[str] = field(default_factory=list)

    @property
    def aggregation(self) -> str | None:
        first = next(iter(self.rows.values()), None)
        return first.aggregation if first else None

    def value(self, internal_id: str, key: str) -> float | None:
        """One figure for one entity; ``None`` when the entity or the figure is absent."""
        row = self.rows.get(internal_id)
        return numeric((row.values or {}).get(key)) if row else None


def provider_stat_rows(
    db: Session,
    internal_ids: list[str],
    *,
    provider: str = "balldontlie",
    entity_type: str = "player",
    season: str | Season | None = None,
    season_type: SeasonType = SeasonType.REGULAR,
    stat_group: str = "general/advanced",
    now: datetime | None = None,
) -> ProviderStatRows:
    if entity_type not in ("player", "team"):
        raise ValueError(f"unknown entity_type {entity_type!r}")
    label = resolve_season(season)
    scope = StatScope(Season.parse(label), season_type)
    requested = list(dict.fromkeys(internal_ids))
    rows: dict[str, ProviderStatSnapshot] = {}
    if requested:
        for row in db.scalars(
            select(ProviderStatSnapshot).where(
                ProviderStatSnapshot.provider == provider,
                ProviderStatSnapshot.entity_type == entity_type,
                ProviderStatSnapshot.internal_id.in_(requested),
                ProviderStatSnapshot.season == label,
                ProviderStatSnapshot.season_type == season_type.value,
                ProviderStatSnapshot.stat_group == stat_group,
            )
        ).all():
            rows[row.internal_id] = row
    missing = [i for i in requested if i not in rows]
    notes = [f"{provider} {stat_group}: figures are as the provider defines them"]
    if missing:
        notes.append(
            f"{len(missing)} of {len(requested)} requested {entity_type}s have no {provider} "
            f"{stat_group} row for {scope}; they are absent, not zero"
        )
    meta = build_source_meta(
        "player_stats" if entity_type == "player" else "team_stats",
        rows.values(),
        season=label,
        season_type=season_type.value,
        expected_count=len(requested) or None,
        notes=notes,
        now=now,
    )
    return ProviderStatRows(provider, scope, stat_group, entity_type, rows, requested, meta, missing)
